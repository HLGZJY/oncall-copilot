"""M5-T7 mock e2e 2 剧本（m5 issue 07）——cpu-spike + slow-sql 全链路（D-47/G9）。

golden dev 剧本驱动 MockPlanner（零 LLM）→ 调查收束 execute_action 干跑建
pending proposal → POST /remediations/{id}/confirm approve（替身执行器 +
Fetcher 替身观察窗快照）→ 恢复验证 → proposal recovered + incident mitigated；
slow-sql 补未恢复变体（回滚 → 复验仍失败 → escalated + incident 保持
investigating，D-28）。golden `remediation` 与 runbook action/rollback 对账
（2 剧本 × 字段级）。

零真实 HTTP / 零真实 sleep（观察窗快照替身，真实窗口归 issue 08）；活栈真
实验证归 T8。复用 test_m5_acceptance_gates 的组装夹具（同票共享替身纪律）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import test_m5_acceptance_gates as gates
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import golden_support
from oncall.api.remediation import RemediationDeps
from oncall.db import Incident
from oncall.ingest.app import create_app
from oncall.remediation.executor import ControlledExecutor
from oncall.remediation.runbook import load_runbook_library
from oncall.remediation.verifier import RunbookRecoveryVerifier

pytestmark = pytest.mark.inproc_asgi

RUNBOOKS_DIR = golden_support.GOLDEN_DEV_DIR.parents[2] / "remediation" / "runbooks"
EXECUTION_KEYS = {"executed", "rejected", "ok", "output_summary"}


class FakeFetcher:
    """观察窗快照替身：values 即窗内全部样本（不真实等待；真实窗口归 08）。"""

    def __init__(self, values: list[str]) -> None:
        self.values = values
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, params: dict[str, str], timeout: float) -> Any:
        self.calls.append({"url": url, "params": params})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {},
                            "values": [[1_000_000 + i, v] for i, v in enumerate(self.values)],
                        }
                    ]
                },
            },
        )


def _recording_runner():
    """runner 替身：记录 argv 零真实 subprocess（A6 语义由 issue 05 单测钉死）。"""
    calls: list[list[str]] = []

    def runner(argv: list[str], **kw: Any) -> SimpleNamespace:
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

    return runner, calls


def _recovered_deps(scenario: str, values: list[str]) -> tuple[RemediationDeps, list[list[str]]]:
    """恢复路径注入：真实 ControlledExecutor（runner 替身）+ 真实 RunbookRecoveryVerifier
    （FakeFetcher 判据满足快照）。$var 运行时参数经 runtime_values 注入：
    slow-sql 的 $session_id / cpu-spike 的 $cpuset_cores（原核从记录文件读取）。"""
    runner, calls = _recording_runner()
    runtime = {"session_id": "4242"} if scenario == "slow-sql" else {"cpuset_cores": "0-3"}
    executor = ControlledExecutor(runner=runner, runtime_values=runtime)
    fetcher = FakeFetcher(values)
    library = load_runbook_library(RUNBOOKS_DIR)
    verifier = RunbookRecoveryVerifier(
        fetcher,
        base_url="http://prometheus:9090",
        timeout=1.0,
        runbooks=library,
        reference_values={"db_pool_size": 20.0},
    )
    deps = RemediationDeps(executor=executor, verifier=verifier, runbooks=library)
    return deps, calls


# ---------------------------------------------------------------------------
# mock e2e：cpu-spike / slow-sql 恢复路径全链路
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "locator", "values"),
    [
        ("cpu-spike", "cpu-spike/stop-stress-and-restore-cpuset", ["0.012", "0.03", "0.049"]),
        ("slow-sql", "slow-sql/kill-lock-session", ["12.5", "15"]),
    ],
)
def test_mock_e2e_full_chain_recovered(scenario: str, locator: str, values: list[str]):
    """调查收束干跑 → pending → confirm approve → 受控执行 → 恢复验证 →
    proposal recovered + incident mitigated（2 剧本端到端 mock 收口，T8 前置）。"""
    engine = gates._make_engine()
    deps, argv_calls = _recovered_deps(scenario, values)
    incident_id = gates._investigate_with_execute(engine, scenario, locator, remediation=deps)
    client = TestClient(_app(engine, deps))
    row0 = gates._get_proposal(engine, incident_id)
    assert row0.status == "pending"

    resp = client.get(f"/remediations/{row0.id}")
    assert resp.status_code == 200
    row = client.post(
        f"/remediations/{row0.id}/confirm", json={"decision": "approve", "reason": "e2e"}
    ).json()
    assert row["status"] == "recovered"
    assert row["decision"] == "approve" and row["finished_at"]
    execution = row["params_json"]["execution"]
    assert set(execution) == EXECUTION_KEYS
    assert execution["ok"] is True and not execution["rejected"]  # 白名单全放行
    assert set(row["verify_result_json"]) == {
        "recovered",
        "promql",
        "condition",
        "window_s",
        "observed",
        "samples",
    }
    assert row["verify_result_json"]["recovered"] is True
    with Session(engine) as session:
        assert session.get(Incident, incident_id).status == "mitigated"
    if scenario == "slow-sql":
        assert any("4242" in argv[-1] for argv in argv_calls)  # $session_id 执行期注入
    else:
        # $cpuset_cores 执行期注入
        assert any("--cpuset-cpus=0-3" in a for argv in argv_calls for a in argv)


def _app(engine: Any, deps: RemediationDeps) -> Any:
    return create_app(engine, remediation=deps)


# ---------------------------------------------------------------------------
# mock e2e：slow-sql 未恢复变体（回滚 → 复验失败 → escalated，D-28）
# ---------------------------------------------------------------------------


def test_mock_e2e_slow_sql_not_recovered_rollback_escalated():
    """判据不满足 → runbook 显式 rollback（$session_id 注入）→ 复验仍失败 →
    escalated + rollback_status=rolled_back + incident 保持 investigating。"""
    engine = gates._make_engine()
    runner, _argv_calls = _recording_runner()
    executor = ControlledExecutor(runner=runner, runtime_values={"session_id": "4242"})
    library = load_runbook_library(RUNBOOKS_DIR)
    deps = RemediationDeps(
        executor=executor,
        # 首验+复验均未恢复
        verifier=gates.ScriptedVerifier([gates._verify(False), gates._verify(False)]),
        runbooks=library,
    )
    incident_id = gates._investigate_with_execute(
        engine, "slow-sql", "slow-sql/kill-lock-session", remediation=deps
    )
    row = (
        TestClient(_app(engine, deps))
        .post(
            f"/remediations/{gates._get_proposal(engine, incident_id).id}/confirm",
            json={"decision": "approve"},
        )
        .json()
    )
    assert row["status"] == "escalated"
    assert row["rollback_status"] == "rolled_back"
    rollback = row["params_json"]["rollback"]
    assert set(rollback) == EXECUTION_KEYS
    assert rollback["executed"] and "4242" in rollback["executed"][0]["argv"][-1]
    assert "mysql.kill_session" in rollback["executed"][0]["action"]
    with Session(engine) as session:
        assert session.get(Incident, incident_id).status == "investigating"  # D-28


# ---------------------------------------------------------------------------
# golden `remediation` 与 runbook action/rollback 对账（2 剧本 × 字段级）
# ---------------------------------------------------------------------------


def test_golden_remediation_runbook_alignment():
    """golden `remediation` 处置描述 ↔ runbook actions/rollback/verification 字段级
    对账（alert_ref 对齐 golden 首条告警；判据/观察窗与实测回落值对齐）。"""
    library = load_runbook_library(RUNBOOKS_DIR)

    doc = golden_support.load_golden("cpu-spike")
    rb = library["cpu-spike"]
    prose = doc["remediation"]
    assert rb.alert_ref == doc["runs"][0]["alert_timeline"][0]["alert_name"]
    assert [s.action for a in rb.actions for s in a.steps] == [
        "docker.remove_container",
        "docker.remove_container",
        "docker.restore_cpuset",
    ]
    assert "探针" in prose and "Pumba" in prose and "cpuset" in prose  # 动作语义对齐
    assert rb.rollback == []  # cpu-spike 处置即恢复，rollback 显式空（D-45）
    assert rb.verification.condition == "p95 <= 0.05" and rb.verification.window_s == 60
    assert "P95" in prose and "0.05" in prose and "histogram_quantile" in rb.verification.promql

    doc = golden_support.load_golden("slow-sql")
    rb = library["slow-sql"]
    prose = doc["remediation"]
    assert rb.alert_ref == doc["runs"][0]["alert_timeline"][0]["alert_name"]
    assert [s.action for a in rb.actions for s in a.steps] == [
        "docker.remove_container",
        "mysql.kill_session",
    ]
    assert "KILL" in prose and "排空" in prose  # 动作语义对齐
    assert [(s.action, s.params["session_id"]) for s in rb.rollback] == [
        ("mysql.kill_session", "$session_id")
    ]
    assert rb.verification.promql == "avg_over_time(demo_db_pool_used[1m])"
    assert rb.verification.condition == "db_pool_used < db_pool_size"
    assert rb.verification.window_s == 60 and "连接池" in prose
