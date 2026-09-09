"""M5-T7 验收断言收口（m5 issue 07）——设计文档「验收标准」节逐条机械断言。

断言汇总票落点（照 M4-T7 先例）：只补「跨 issue 端到端」缺口——干跑不落地 /
100% 过确认门 / 恢复验证恢复侧跨层收口 / 全程留痕逐字段对账（未恢复侧
escalated 归 test_m5_mock_e2e；冻结面与白名单收口见另两个 m5 文件）。
零 LLM、零真实外呼（golden 替身 + MockPlanner + 替身执行器/验证器）。
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

import golden_support
from oncall.api.investigation import InvestigationDeps
from oncall.api.remediation import RemediationDeps
from oncall.db import AlertEvent, Incident, create_tables
from oncall.db.models import EvidenceStep as EvidenceStepRow
from oncall.db.models import RemediationProposal
from oncall.harness.planner import MockPlanner, PlannerDecision
from oncall.harness.tools.execute import build_execute_action_handler
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict
from oncall.ingest.app import create_app
from oncall.remediation import service
from oncall.remediation.runbook import load_runbook_library

pytestmark = pytest.mark.inproc_asgi

RUNBOOKS_DIR = Path(__file__).resolve().parents[2] / "remediation" / "runbooks"

EXECUTION_KEYS = {"executed", "rejected", "ok", "output_summary"}
VERIFY_KEYS = {"recovered", "promql", "condition", "window_s", "observed", "samples"}


def _make_engine() -> Any:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return engine


def _seed_incident(engine: Any, scenario: str) -> int:
    """建档：golden 首条告警落 alert_events + 1 条 incident。"""
    entry = golden_support.load_golden(scenario)["runs"][0]["alert_timeline"][0]
    with Session(engine) as session:
        row = AlertEvent(
            fingerprint=f"fp-{scenario}",
            source="alertmanager",
            labels_json=entry["labels"],
            annotations_json={},
            fired_at=datetime.fromisoformat(entry["fired_at"]),
            status="deduped",
        )
        session.add(row)
        session.flush()
        incident = Incident(alert_ids=[row.id], severity="warning", status="investigating")
        session.add(incident)
        session.commit()
        return incident.id


def _db_creator(engine: Any, incident_id: int) -> Any:
    """proposal 落库接缝（C3：闭包独立 Session，鸭子类型满足 ProposalCreator）。"""

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

    return creator


def _planner(doc: dict[str, Any], locator: str) -> MockPlanner:
    """MockPlanner：末步工具换 execute_action（零 LLM，结论 = golden root_cause）。"""
    iso = golden_support.golden_time_anchor(doc).isoformat()
    script: list[PlannerDecision] = []
    thoughts = doc["investigation_path"]
    for i, thought in enumerate(thoughts):
        if i == len(thoughts) - 1:
            script.append(
                PlannerDecision.model_validate(
                    {
                        "thought": thought,
                        "next_tool": "execute_action",
                        "args": {"action": locator, "params": {}},
                    }
                )
            )
        else:
            script.append(
                PlannerDecision.model_validate(
                    {
                        "thought": thought,
                        "next_tool": "query_metrics",
                        "args": {"promql": "up", "start": iso, "end": iso},
                    }
                )
            )
    script.append(
        PlannerDecision.model_validate({"thought": "证据已足够", "conclusion": doc["root_cause"]})
    )
    return MockPlanner(script=script)


class RecordingExecutor:
    """替身执行器：只记录调用（demo 侧零副作用的断言对象）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, dry_run_json: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(dry_run_json))
        return {"executed": [], "rejected": [], "ok": True, "output_summary": "stub"}


class ScriptedVerifier:
    """替身验证器：按剧本回放验证结果（issue 06 形状）。"""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = list(results)

    def verify(self, dry_run_json: dict[str, Any]) -> dict[str, Any]:
        return self._results.pop(0)


def _verify(recovered: bool) -> dict[str, Any]:
    return {
        "recovered": recovered,
        "promql": "avg_over_time(demo_db_pool_used[1m])",
        "condition": "db_pool_used < db_pool_size",
        "window_s": 60,
        "observed": 12.5 if recovered else 25.0,
        "samples": 4,
    }


def _investigate_with_execute(
    engine: Any, scenario: str, locator: str, remediation: RemediationDeps | None = None
) -> int:
    """调查收束 execute_action 干跑：返回 incident_id，proposal 落 pending。"""
    doc = golden_support.load_golden(scenario)
    incident_id = _seed_incident(engine, scenario)
    library = load_runbook_library(RUNBOOKS_DIR)

    registry = ToolRegistry(now=lambda: datetime.now(UTC))
    n = len(doc["investigation_path"])
    verdicts = [VerifierVerdict(supported=False, reason="证据不足")] * (n - 1)
    verdicts.append(VerifierVerdict(supported=True, reason="证据支持"))
    register_six_tools(
        registry,
        {
            **golden_support.make_golden_handlers(doc),
            "execute_action": build_execute_action_handler(
                runbook_loader=library.get, proposal_creator=_db_creator(engine, incident_id)
            ),
        },
    )
    components = golden_support.LoopComponents(
        planner=_planner(doc, locator),
        registry=registry,
        gate=golden_support.PermissionGate(now=lambda: datetime.now(UTC)),
        verifier=Verifier(judge=MockVerifierJudge(script=verdicts, default=verdicts[-1])),
        now=lambda: datetime.now(UTC),
    )
    app = create_app(
        engine, investigation=InvestigationDeps(components=components), remediation=remediation
    )
    resp = TestClient(app).post("/investigate", json={"incident_id": incident_id})
    assert resp.status_code == 200 and resp.json()["termination"] == "concluded"
    return incident_id


def _get_proposal(engine: Any, incident_id: int) -> RemediationProposal:
    """取 incident 唯一 proposal 行（expunge 脱管供断言）。"""
    with Session(engine) as session:
        rows = service.list_by_incident(session, incident_id)
        assert len(rows) == 1
        session.expunge(rows[0])
        return rows[0]


def test_dry_run_only_no_side_effects_and_toolresult_shape():
    """验收「干跑不落地」：execute_action 只产干跑——替身执行器零调用、ToolResult
    ok+预览+proposal_id、proposal 落 pending 且 dry_run_json 与预览 deep-equal。"""
    engine = _make_engine()
    executor = RecordingExecutor()
    deps = RemediationDeps(executor=executor, verifier=ScriptedVerifier([]))
    incident_id = _investigate_with_execute(
        engine, "cpu-spike", "cpu-spike/stop-stress-and-restore-cpuset", remediation=deps
    )
    assert executor.calls == []  # 循环内零执行
    body = (
        TestClient(create_app(engine, remediation=deps))
        .get(f"/remediations?incident_id={incident_id}")
        .json()
    )
    assert body["count"] == 1
    row = body["items"][0]
    assert row["status"] == "pending"
    assert row["params_json"] is None  # 未确认前无执行留痕
    preview = row["dry_run_json"]
    assert set(preview) == {"runbook_slug", "action_id", "action_name", "commands", "impact"}
    assert [c["action"] for c in preview["commands"]] == [
        "docker.remove_container",
        "docker.remove_container",
        "docker.restore_cpuset",
    ]
    assert all(c["impact"] for c in preview["commands"])  # 影响面逐条渲染
    # ToolResult 三键形状（证据步 output_json 回溯）
    with Session(engine) as session:
        step = (
            session.query(EvidenceStepRow)
            .filter_by(incident_id=incident_id, tool="execute_action")
            .one()
        )
    assert step.output_json["status"] == "ok"
    assert set(step.output_json["data"]) == {"dry_run_preview", "proposal_id"}
    assert step.output_json["data"]["proposal_id"] == str(row["id"])
    assert step.output_json["data"]["dry_run_preview"] == preview  # 批准对象锁定


def test_unapproved_proposal_cannot_reach_executor():
    """验收「100% 过确认门」端到端：reject 后不可再 approve（409）、pending→executing
    直跳被状态机拒绝、执行器替身始终零调用。"""
    engine = _make_engine()
    executor = RecordingExecutor()
    deps = RemediationDeps(executor=executor, verifier=ScriptedVerifier([]))
    incident_id = _investigate_with_execute(
        engine, "cpu-spike", "cpu-spike/stop-stress-and-restore-cpuset", remediation=deps
    )
    client = TestClient(create_app(engine, remediation=deps))
    pid = int(_get_proposal(engine, incident_id).id)

    assert (
        client.post(
            f"/remediations/{pid}/confirm", json={"decision": "reject", "reason": "不批"}
        ).json()["status"]
        == "rejected"
    )
    assert executor.calls == []  # 确认前零执行

    assert (
        client.post(f"/remediations/{pid}/confirm", json={"decision": "approve"}).status_code == 409
    )
    assert executor.calls == []  # 终态再确认不触达执行器

    with pytest.raises(service.ProposalStateError):
        with Session(engine) as session:
            service.transition(session, session.get(RemediationProposal, pid), "executing")


def test_full_audit_trail_field_by_field():
    """验收「全程留痕」+「恢复验证」恢复侧跨层收口：proposal 行逐字段对账
    （D-39 不可变 + issue 05 审计形状 + issue 06 verify 形状），incident 翻 mitigated。"""
    engine = _make_engine()
    executor = RecordingExecutor()
    deps = RemediationDeps(
        executor=executor,
        verifier=ScriptedVerifier([_verify(True)]),
        runbooks=load_runbook_library(RUNBOOKS_DIR),
    )
    incident_id = _investigate_with_execute(
        engine, "slow-sql", "slow-sql/kill-lock-session", remediation=deps
    )
    client = TestClient(create_app(engine, remediation=deps))
    before = copy.deepcopy(_get_proposal(engine, incident_id).dry_run_json)

    row = client.post(
        f"/remediations/{_get_proposal(engine, incident_id).id}/confirm",
        json={"decision": "approve", "reason": "golden 对账通过"},
    ).json()
    assert row["status"] == "recovered"
    assert row["dry_run_json"] == before  # D-39：dry_run_json 原文不可变（deep-equal）
    assert (row["decision"], row["confirm_reason"]) == ("approve", "golden 对账通过")
    assert row["confirmed_at"] and row["executed_at"] and row["finished_at"]
    assert row["rollback_status"] is None  # 恢复路径无回滚
    execution = row["params_json"]["execution"]  # issue 05 审计形状
    assert set(execution) == EXECUTION_KEYS
    assert execution["ok"] is True and not execution["rejected"]
    assert set(row["verify_result_json"]) == VERIFY_KEYS  # issue 06 verify 形状
    assert row["verify_result_json"]["recovered"] is True
    assert executor.calls == [before]  # 执行器收到的唯一输入 = 批准对象原文（D-39 零漂移）
    with Session(engine) as session:
        assert session.get(Incident, incident_id).status == "mitigated"  # D-19/D-46 枚举流转
