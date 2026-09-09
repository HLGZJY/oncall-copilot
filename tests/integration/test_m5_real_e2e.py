"""M5-08 真实 e2e（T8）：活 demo 栈 2 剧本端到端自动处置恢复。

**环境开关**：默认跳过，`ONCALL_RUN_M5_REAL_E2E=1` 才执行（照 M3/M4 issue 08
先例；环境门槛非 key 门槛——处置管线零 LLM，mock 决策脚本驱动）。运行方式::

    ONCALL_RUN_M5_REAL_E2E=1 python -m pytest tests/integration/test_m5_real_e2e.py

口径（设计 G2/G6/G9、D-39/D-40/D-42/D-44/D-45/D-28）：
- **真实面**：chaos 注入（bash 剧本）→ 告警 firing 实查 → mock 决策调查收束
  execute_action 干跑（proposal pending）→ **真实 HTTP**（uvicorn + curl）
  confirm approve → 真实 ControlledExecutor（白名单 argv → docker/mysql CLI
  打 demo 容器）→ 真实 RunbookRecoveryVerifier（Prometheus 回查，观察窗轮询
  代理直到窗口干净）→ proposal recovered + incident mitigated。
- **mock 面**（纪律，非偷工）：调查决策是确定性系统行为的对偶面——MockPlanner
  剧本驱动 + golden 证据（D-18），本票验证的是闸门管线不是决策质量。
- **实测回填（禁虚构）**：处置耗时（confirm 请求）/执行命令数/恢复窗口
  （轮询回查次数 × 间隔）→ `.scratch/tmp/m5-08-real-e2e-report.json`。
- 写操作副作用边界：仅 demo 容器内 + 白名单动作族；finally 必跑 cleanup.sh
  （最坏情况重建 demo 栈，blast radius 封顶）。
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from m5_real_support import (
    LIVE_PROM_URL,
    LiveServer,
    PollingRecoveryVerifier,
    curl_json,
    docker_info_ncpu,
    docker_inspect_cpuset,
    mysql_lock_session_id,
    prom_instant,
    run_bash,
    wait_alert_firing,
)
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

import golden_support
from oncall.api.investigation import InvestigationDeps
from oncall.api.remediation import RemediationDeps
from oncall.db import AlertEvent, Incident, create_tables
from oncall.harness.planner import MockPlanner, PlannerDecision
from oncall.harness.tools.execute import build_execute_action_handler
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict
from oncall.infra.http import HttpxFetcher
from oncall.ingest.app import create_app
from oncall.remediation import service
from oncall.remediation.executor import ControlledExecutor
from oncall.remediation.runbook import load_runbook_library
from oncall.remediation.verifier import RunbookRecoveryVerifier

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ONCALL_RUN_M5_REAL_E2E") != "1",
        reason="活栈真实 e2e 须显式 ONCALL_RUN_M5_REAL_E2E=1（环境开关，开工前需确认 demo 栈就绪）",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS_DIR = REPO_ROOT / "remediation" / "runbooks"
CHAOS_DIR = REPO_ROOT / "chaos" / "scenarios"
REPORT_PATH = REPO_ROOT / ".scratch" / "tmp" / "m5-08-real-e2e-report.json"
CONFIRM_PORT = 8123

SCENARIOS: dict[str, dict[str, Any]] = {
    "cpu-spike": {
        "alert": "DemoApiGwHighLatency",
        "locator": "cpu-spike/stop-stress-and-restore-cpuset",
        "inject_env": {"DURATION_SEC": "420"},
        "container": "oncall-demo-api-gw-1",
    },
    "slow-sql": {
        "alert": "DemoDbPoolSaturated",
        "locator": "slow-sql/kill-lock-session",
        "inject_env": {"DURATION": "420"},
        "container": "oncall-demo-mysql-1",
    },
}


# ---------------------------------------------------------------------------
# 调查侧组装（照 tests/e2e/test_m5_acceptance_gates.py 先例，mock 决策纪律）
# ---------------------------------------------------------------------------


def _make_engine() -> Any:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return engine


def _seed_incident(engine: Any, scenario: str) -> int:
    entry = golden_support.load_golden(scenario)["runs"][0]["alert_timeline"][0]
    with Session(engine) as session:
        row = AlertEvent(
            fingerprint=f"fp-real-{scenario}",
            source="alertmanager",
            labels_json=entry["labels"],
            annotations_json={},
            fired_at=datetime.now(UTC),
            status="deduped",
        )
        session.add(row)
        session.flush()
        incident = Incident(alert_ids=[row.id], severity="warning", status="investigating")
        session.add(incident)
        session.commit()
        return incident.id


def _investigate_to_pending(engine: Any, scenario: str, locator: str) -> int:
    """mock 决策调查收束 execute_action 干跑 → proposal pending（返回 incident_id）。"""
    doc = golden_support.load_golden(scenario)
    incident_id = _seed_incident(engine, scenario)
    library = load_runbook_library(RUNBOOKS_DIR)

    def creator(payload: dict[str, Any]) -> str:
        with Session(engine) as session:
            return str(
                service.create_proposal(
                    session,
                    incident_id=incident_id,
                    runbook_slug=payload["runbook_slug"],
                    action_id=payload["action_id"],
                    dry_run_json=payload["dry_run_json"],
                )
            )

    registry = ToolRegistry(now=lambda: datetime.now(UTC))
    n = len(doc["investigation_path"])
    verdicts = [VerifierVerdict(supported=False, reason="证据不足")] * (n - 1)
    verdicts.append(VerifierVerdict(supported=True, reason="证据支持"))
    iso = golden_support.golden_time_anchor(doc).isoformat()
    script: list[PlannerDecision] = []
    for i, thought in enumerate(doc["investigation_path"]):
        last = i == n - 1
        script.append(
            PlannerDecision.model_validate(
                {
                    "thought": thought,
                    "next_tool": "execute_action" if last else "query_metrics",
                    "args": (
                        {"action": locator, "params": {}}
                        if last
                        else {"promql": "up", "start": iso, "end": iso}
                    ),
                }
            )
        )
    script.append(
        PlannerDecision.model_validate({"thought": "证据已足够", "conclusion": doc["root_cause"]})
    )
    register_six_tools(
        registry,
        {
            **golden_support.make_golden_handlers(doc),
            "execute_action": build_execute_action_handler(
                runbook_loader=library.get, proposal_creator=creator
            ),
        },
    )
    components = golden_support.LoopComponents(
        planner=MockPlanner(script=script),
        registry=registry,
        gate=golden_support.PermissionGate(now=lambda: datetime.now(UTC)),
        verifier=Verifier(judge=MockVerifierJudge(script=verdicts, default=verdicts[-1])),
        now=lambda: datetime.now(UTC),
    )
    app = create_app(engine, investigation=InvestigationDeps(components=components))
    resp = TestClient(app).post("/investigate", json={"incident_id": incident_id})
    assert resp.status_code == 200 and resp.json()["termination"] == "concluded"
    return incident_id


# ---------------------------------------------------------------------------
# 真实 e2e 主流程
# ---------------------------------------------------------------------------


def _chaos_script(scenario: str, name: str) -> Path:
    """chaos 剧本脚本定位（目录带 NN- 序号前缀）。"""
    matches = sorted((CHAOS_DIR).glob(f"*-{scenario}"))
    assert len(matches) == 1, f"chaos 剧本目录不唯一：{scenario}"
    return matches[0] / name


def _preflight() -> None:
    assert prom_instant(HttpxFetcher(), "up") is not None, "Prometheus 不可查（活栈门槛）"


def _runtime_values(scenario: str) -> tuple[dict[str, str], dict[str, float]]:
    """注入前收集运行时参数与参考值（实测，禁虚构）。"""
    if scenario == "cpu-spike":
        orig = docker_inspect_cpuset("oncall-demo-api-gw-1")
        if not orig:  # 未收窄过 → 全核兜底（cleanup.sh 同语义）
            orig = f"0-{docker_info_ncpu() - 1}"
        return {"cpuset_cores": orig}, {}
    pool_size = prom_instant(HttpxFetcher(), "demo_db_pool_size")
    assert pool_size is not None, "demo_db_pool_size 无样本（活栈门槛）"
    return {}, {"db_pool_size": pool_size}


def _run_real_e2e(scenario: str) -> dict[str, Any]:
    spec = SCENARIOS[scenario]
    _preflight()
    run_bash(_chaos_script(scenario, "cleanup.sh"))  # 清残留，保证净态注入

    runtime_values, reference_values = _runtime_values(scenario)
    run_bash(_chaos_script(scenario, "inject.sh"), env_extra=spec["inject_env"])
    alert_wait_s = wait_alert_firing(HttpxFetcher(), spec["alert"])
    if scenario == "slow-sql":
        runtime_values["session_id"] = mysql_lock_session_id(spec["container"])

    library = load_runbook_library(RUNBOOKS_DIR)
    executor = ControlledExecutor(runtime_values=runtime_values)
    verifier = PollingRecoveryVerifier(
        RunbookRecoveryVerifier(
            HttpxFetcher(),
            base_url=LIVE_PROM_URL,
            timeout=5.0,
            runbooks=library,
            reference_values=reference_values,
        )
    )
    engine = _make_engine()
    incident_id = _investigate_to_pending(engine, scenario, spec["locator"])
    app = create_app(
        engine,
        remediation=RemediationDeps(executor=executor, verifier=verifier, runbooks=library),
    )
    server = LiveServer(app, CONFIRM_PORT)
    try:
        base = f"http://127.0.0.1:{CONFIRM_PORT}"
        with Session(engine) as session:
            rows = service.list_by_incident(session, incident_id)
            assert len(rows) == 1 and rows[0].status == "pending"
            proposal_id = rows[0].id
        got = curl_json("GET", f"{base}/remediations/{proposal_id}")
        assert got["status"] == "pending"

        started = time.monotonic()
        row = curl_json(
            "POST",
            f"{base}/remediations/{proposal_id}/confirm",
            payload={"decision": "approve", "reason": "M5-T8 real e2e"},
        )
        confirm_s = round(time.monotonic() - started, 1)
    finally:
        server.stop()
        run_bash(_chaos_script(scenario, "cleanup.sh"))

    execution = row["params_json"]["execution"]
    verify = row["verify_result_json"] or {}
    (REPO_ROOT / ".scratch" / "tmp" / "m5-08-last-row.json").write_text(
        json.dumps(row, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    assert row["status"] == "recovered", row  # 恢复路径主断言
    assert row["decision"] == "approve" and row["finished_at"]
    assert execution["ok"] is True and not execution["rejected"]
    assert verify.get("recovered") is True
    with Session(engine) as session:
        assert session.get(Incident, incident_id).status == "mitigated"

    attempts = int(verify.get("poll_attempts", 1))
    record = {
        "scenario": scenario,
        "proposal_id": proposal_id,
        "incident_id": incident_id,
        "status": row["status"],
        "alert_wait_s": alert_wait_s,
        "confirm_duration_s": confirm_s,
        "executed_commands": len(execution["executed"]),
        "rejected_commands": len(execution["rejected"]),
        "recovery_window_s": round((attempts - 1) * 15.0, 1),
        "verify_poll_attempts": attempts,
        "verify_observed": verify.get("observed"),
        "verify_samples": verify.get("samples"),
        "runtime_values": runtime_values,
        "reference_values": reference_values,
        "argv": [item.get("argv") for item in execution["executed"]],
    }
    golden_support.append_report(REPORT_PATH, {"segment": "real-e2e", **record})
    return record


def test_real_e2e_cpu_spike_recovered_mitigated():
    record = _run_real_e2e("cpu-spike")
    assert record["executed_commands"] == 3  # 停探针 + 停 Pumba + 恢复 cpuset
    assert record["rejected_commands"] == 0


def test_real_e2e_slow_sql_recovered_mitigated():
    record = _run_real_e2e("slow-sql")
    assert record["executed_commands"] == 2  # 停探针 + KILL 会话
    assert record["rejected_commands"] == 0
    assert record["runtime_values"]["session_id"].isdigit()
