"""恢复验证器 + confirm 同步链回滚编排测试（M5 issue 06 / T6 / G6+G7 / D-44 / D-45 / D-28）。

契约来源：docs/design/m5-remediation-gates-design.md §G6（判据来源 = runbook
verification 显式声明，不从锚定告警解析、不从调查结论推导）/ §G7（回滚动作
来源 = runbook 显式 rollback，系统不做逆操作推导；回滚同样过白名单 + 留痕）+
decisions.md **D-44**（判据显式声明）/ **D-45**（rollback=[] 合法）/ **D-28**
（转人工不是丢弃：escalated 落点 + incident 保持 investigating）/ **D-40**
（confirm 即执行同步链）。

覆盖（对应 issue 06 验收①–⑤）：
- verifier：Fetcher 替身（Prometheus matrix JSON）恢复/未恢复两条路径；
  condition 机械判定边界（==阈值 / 严格不等）；观察窗 query_range 参数断言；
  未知 runbook / condition 不可解析 / 无样本 / 回查失败全按未恢复 fail-closed；
  脏 runbook（缺 verification）在解析层即拒绝
- 回滚编排：未恢复 → rollback 经白名单执行器 → 复验通过 → recovered；
  复验仍失败 → escalated + incident 保持 investigating；rollback=[] 直边
  escalated；白名单拒绝 → 不执行 + 审计 + escalated；$session_id 经
  runtime_values 注入（与 actions 同一来源）

零 LLM、零真实 HTTP 外呼（Fetcher 替身普通类，A1 禁 import httpx）；
60s 观察窗不真实 sleep——替身直接返回窗口快照，真实等待归 issue 08。
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import StaticPool, create_engine, event
from sqlalchemy.orm import Session

from oncall.db import Incident, create_tables
from oncall.db.models import RemediationProposal
from oncall.infra.http import HttpResponse
from oncall.remediation import service
from oncall.remediation.executor import ControlledExecutor
from oncall.remediation.runbook import RunbookValidationError, load_runbook_library, parse_runbook
from oncall.remediation.service import create_proposal, start_execution
from oncall.remediation.verifier import (
    ROLLBACK_BLOCKED,
    ROLLBACK_ROLLED_BACK,
    ROLLBACK_SKIPPED,
    RunbookRecoveryVerifier,
    run_confirm_chain,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS_DIR = REPO_ROOT / "remediation" / "runbooks"
RUNBOOKS = load_runbook_library(RUNBOOKS_DIR)  # 真实定稿 runbook 库（只读）

CPU_SLUG = "cpu-spike"
SLOW_SQL_SLUG = "slow-sql"


# ---------------------------------------------------------------------------
# Fetcher 替身（Prometheus matrix JSON；M3 取证同款接缝）
# ---------------------------------------------------------------------------


class FakeFetcher:
    """返回窗口快照替身：values 即观察窗内全部样本（不真实等待）。"""

    def __init__(self, values: list[str], *, status_code: int = 200) -> None:
        self.values = values
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, params: dict[str, str], timeout: float) -> HttpResponse:
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        if self.status_code != 200:
            return HttpResponse(status_code=self.status_code, body="upstream broken")
        payload = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {},
                        "values": [[1_000_000 + i, v] for i, v in enumerate(self.values)],
                    }
                ]
            },
        }
        return HttpResponse(status_code=200, body=json.dumps(payload))


def _make_verifier(
    fetcher: FakeFetcher,
    *,
    runbooks: dict[str, Any] | None = None,
    reference_values: dict[str, float] | None = None,
) -> RunbookRecoveryVerifier:
    return RunbookRecoveryVerifier(
        fetcher,
        base_url="http://prometheus:9090",
        timeout=2.0,
        runbooks=runbooks if runbooks is not None else load_runbook_library(RUNBOOKS_DIR),
        reference_values=reference_values,
    )


def _dry_run(slug: str) -> dict[str, Any]:
    return {"runbook_slug": slug, "action_id": "stub-action"}


# ---------------------------------------------------------------------------
# verifier：恢复/未恢复两条路径 + condition 机械判定边界（验收①④）
# ---------------------------------------------------------------------------


class TestVerifierPaths:
    def test_recovered_path_returns_full_shape(self):
        fetcher = FakeFetcher(["0.012", "0.03", "0.049"])  # 全部 ≤ 0.05
        verifier = _make_verifier(fetcher)

        result = verifier.verify(_dry_run(CPU_SLUG))

        assert result["recovered"] is True
        expected_promql = (
            "histogram_quantile(0.95, sum by (le) (rate(demo_request_duration_seconds_bucket"
            '{endpoint!="/tasks"}[1m])))'
        )
        assert result["promql"] == expected_promql
        assert result["condition"] == "p95 <= 0.05"
        assert result["window_s"] == 60
        assert result["observed"] == 0.049  # 窗口内最劣样本
        assert result["samples"] == 3
        assert "error" not in result

    def test_not_recovered_when_threshold_exceeded(self):
        verifier = _make_verifier(FakeFetcher(["0.012", "0.095"]))

        result = verifier.verify(_dry_run(CPU_SLUG))

        assert result["recovered"] is False
        assert result["observed"] == 0.095

    def test_condition_boundary_equal_threshold_with_le_recovers(self):
        verifier = _make_verifier(FakeFetcher(["0.05"]))  # == 阈值，<= 判满足

        assert verifier.verify(_dry_run(CPU_SLUG))["recovered"] is True

    def test_slow_sql_strict_lt_boundary_at_pool_size_not_recovered(self):
        verifier = _make_verifier(
            FakeFetcher(["12.5", "20"]),  # 20 == 池上限，严格 < 不满足
            reference_values={"db_pool_size": 20.0},
        )

        result = verifier.verify(_dry_run(SLOW_SQL_SLUG))

        assert result["recovered"] is False
        assert result["condition"] == "db_pool_used < db_pool_size"

    def test_query_params_echo_runbook_promql_and_window(self):
        fetcher = FakeFetcher(["0.01"])
        verifier = _make_verifier(fetcher)
        before = datetime.now(UTC)

        verifier.verify(_dry_run(CPU_SLUG))

        assert len(fetcher.calls) == 1
        call = fetcher.calls[0]
        assert call["url"] == "http://prometheus:9090/api/v1/query_range"
        runbook = load_runbook_library(RUNBOOKS_DIR)[CPU_SLUG]
        assert call["params"]["query"] == runbook.verification.promql
        start = datetime.fromtimestamp(float(call["params"]["start"]), tz=UTC)
        end = datetime.fromtimestamp(float(call["params"]["end"]), tz=UTC)
        assert timedelta(seconds=58) <= end - start <= timedelta(seconds=62)  # 观察窗 60s
        assert end - before <= timedelta(seconds=5)  # 窗口右端对齐当前时刻

    def test_empty_series_no_signal_fails_closed(self):
        verifier = _make_verifier(FakeFetcher([]))

        result = verifier.verify(_dry_run(CPU_SLUG))

        assert result["recovered"] is False
        assert result["samples"] == 0
        assert "无样本" in result["error"]

    def test_prom_api_error_fails_closed_as_not_recovered(self):
        verifier = _make_verifier(FakeFetcher(["0.01"], status_code=500))

        result = verifier.verify(_dry_run(CPU_SLUG))

        assert result["recovered"] is False
        assert "回查失败" in result["error"]

    def test_unknown_runbook_slug_fails_closed(self):
        verifier = _make_verifier(FakeFetcher(["0.01"]))

        result = verifier.verify(_dry_run("no-such-runbook"))

        assert result["recovered"] is False
        assert "不在验证器 runbook 库" in result["error"]

    def test_unresolvable_condition_fails_closed(self):
        text = (
            "---\nslug: bad-cond\nalert_ref: X\nseverity: warning\n"
            "actions:\n  - id: a\n    name: n\n    steps:\n      - action: "
            "mysql.kill_session\n        params:\n          container: c\n          "
            "session_id: 1\nrollback: []\nverification:\n  promql: 'x'\n  "
            "condition: db_pool_used < unknown_size\n  window_s: 60\n---\n说明\n"
        )
        runbook = parse_runbook(text)
        verifier = _make_verifier(FakeFetcher(["1.0"]), runbooks={"bad-cond": runbook})

        result = verifier.verify(_dry_run("bad-cond"))

        assert result["recovered"] is False
        assert "不可机械解析" in result["error"]

    def test_dirty_runbook_without_verification_rejected_at_parse_layer(self):
        text = (
            "---\nslug: no-verify\nalert_ref: X\nseverity: warning\n"
            "actions:\n  - id: a\n    name: n\n    steps:\n      - action: "
            "mysql.kill_session\n        params:\n          container: c\n          "
            "session_id: 1\nrollback: []\n---\n说明\n"
        )

        with pytest.raises(RunbookValidationError, match="verification"):
            parse_runbook(text)


# ---------------------------------------------------------------------------
# 回滚编排（D-45/G7/D-28；验收②③⑤）
# ---------------------------------------------------------------------------


class RecordingExecutor:
    """受控执行器替身：记录收到的命令清单，返回 ok 审计形状。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, dry_run_json: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(dry_run_json))
        return {
            "executed": [{"step": 1, "action": "stub", "ok": True}],
            "rejected": [],
            "ok": True,
            "output_summary": "stub: allow 1 / reject 0",
        }


class ScriptedVerifier:
    """恢复验证器替身：按脚本顺序返回判定。"""

    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def verify(self, dry_run_json: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(dry_run_json))
        return {
            "recovered": self.outcomes.pop(0),
            "promql": "stub",
            "condition": "x <= 1",
            "window_s": 60,
        }


def _make_engine() -> Any:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_tables(engine)
    return engine


def _seed(engine: Any, *, slug: str = SLOW_SQL_SLUG) -> tuple[int, int]:
    """落 incident + pending proposal，返回 (incident_id, proposal_id)。"""
    with Session(engine) as session:
        incident = Incident(alert_ids=["a" * 64], severity="warning", status="investigating")
        session.add(incident)
        session.commit()
        pid = create_proposal(
            session,
            incident_id=incident.id,
            runbook_slug=slug,
            action_id="kill-lock-session",
            dry_run_json={"runbook_slug": slug, "commands": []},
        )
        return incident.id, pid


def _run_chain(
    engine: Any, proposal_id: int, *, executor: Any, verifier: Any, runbooks: Any
) -> str:
    """同一 Session 内走 approve + start_execution + confirm 同步链（api 层等价序列）。"""
    with Session(engine) as session:
        proposal = session.get(RemediationProposal, proposal_id)
        assert proposal is not None
        service.approve(session, proposal, decision="approve")
        start_execution(session, proposal)
        return run_confirm_chain(
            session, proposal, executor=executor, verifier=verifier, runbooks=runbooks
        )


def _reload(engine: Any, proposal_id: int) -> tuple[RemediationProposal, Incident]:
    with Session(engine) as session:
        proposal = session.get(RemediationProposal, proposal_id)
        assert proposal is not None
        incident = session.get(Incident, proposal.incident_id)
        assert incident is not None
        session.expunge(proposal)
        session.expunge(incident)
        return proposal, incident


class TestRollbackOrchestration:
    def test_not_recovered_rollback_reverify_passes_lands_recovered(self):
        engine = _make_engine()
        _incident_id, pid = _seed(engine)
        executor, verifier = RecordingExecutor(), ScriptedVerifier([False, True])

        final = _run_chain(engine, pid, executor=executor, verifier=verifier, runbooks=RUNBOOKS)

        assert final == "recovered"
        proposal, incident = _reload(engine, pid)
        assert proposal.status == "recovered"
        assert proposal.rollback_status == ROLLBACK_ROLLED_BACK
        assert proposal.finished_at is not None
        assert incident.status == "mitigated"
        # 执行器两次调用：actions（dry_run_json 原文）+ rollback（runbook 显式序列）
        assert len(executor.calls) == 2
        rollback_cmd = executor.calls[1]["commands"][0]
        assert rollback_cmd["action"] == "mysql.kill_session"
        assert rollback_cmd["params"] == {
            "container": "oncall-demo-mysql-1",
            "session_id": "$session_id",
        }
        # 回滚审计留痕落 params_json（与执行留痕同载体）
        assert proposal.params_json["rollback"]["ok"] is True
        # 验证器两次调用（首验 + 复验），复验结果落 verify_result_json
        assert len(verifier.calls) == 2
        assert proposal.verify_result_json["recovered"] is True

    def test_not_recovered_rollback_still_fails_lands_escalated(self):
        engine = _make_engine()
        _incident_id, pid = _seed(engine)
        executor, verifier = RecordingExecutor(), ScriptedVerifier([False, False])

        final = _run_chain(engine, pid, executor=executor, verifier=verifier, runbooks=RUNBOOKS)

        assert final == "escalated"
        proposal, incident = _reload(engine, pid)
        assert proposal.status == "escalated"
        assert proposal.rollback_status == ROLLBACK_ROLLED_BACK  # 回滚已执行，复验仍失败
        assert incident.status == "investigating"  # D-28：转人工不是丢弃

    def test_empty_rollback_direct_edge_to_escalated(self):
        engine = _make_engine()
        _incident_id, pid = _seed(engine, slug=CPU_SLUG)  # cpu-spike rollback=[]
        executor, verifier = RecordingExecutor(), ScriptedVerifier([False])

        final = _run_chain(engine, pid, executor=executor, verifier=verifier, runbooks=RUNBOOKS)

        assert final == "escalated"
        proposal, incident = _reload(engine, pid)
        assert proposal.status == "escalated"
        assert proposal.rollback_status == ROLLBACK_SKIPPED  # 无回滚预案，直边转人工
        assert len(executor.calls) == 1  # 只执行了 actions，没有回滚调用
        assert incident.status == "investigating"

    def test_unknown_runbook_slug_fails_closed_to_escalated(self):
        engine = _make_engine()
        _incident_id, pid = _seed(engine)
        executor, verifier = RecordingExecutor(), ScriptedVerifier([False])

        final = _run_chain(engine, pid, executor=executor, verifier=verifier, runbooks={})

        assert final == "escalated"
        proposal, _incident = _reload(engine, pid)
        assert proposal.rollback_status == ROLLBACK_SKIPPED

    def test_whitelist_rejection_blocks_rollback_and_audits(self):
        engine = _make_engine()
        _incident_id, pid = _seed(engine)
        # 真实受控执行器但未注入 runtime_values → $session_id 解析缺失 → 白名单拒绝
        executor = ControlledExecutor()
        verifier = ScriptedVerifier([False])

        final = _run_chain(engine, pid, executor=executor, verifier=verifier, runbooks=RUNBOOKS)

        assert final == "escalated"
        proposal, incident = _reload(engine, pid)
        assert proposal.status == "escalated"
        assert proposal.rollback_status == ROLLBACK_BLOCKED  # 不执行 + 审计 + 转人工兜底
        rollback_audit = proposal.params_json["rollback"]
        assert rollback_audit["rejected"] and not rollback_audit["executed"]
        assert rollback_audit["ok"] is False
        assert len(verifier.calls) == 1  # 白名单拒绝路径不复验
        assert incident.status == "investigating"

    def test_session_id_runtime_injection_flows_into_rollback_argv(self):
        engine = _make_engine()
        _incident_id, pid = _seed(engine)
        argv_calls: list[list[str]] = []

        def recording_runner(argv: list[str], **kwargs: Any) -> Any:
            argv_calls.append(list(argv))
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        # 与 actions 同一来源：runtime_values 构造期注入（issue 05 先例）
        executor = ControlledExecutor(runner=recording_runner, runtime_values={"session_id": "42"})
        verifier = ScriptedVerifier([False, True])

        _run_chain(engine, pid, executor=executor, verifier=verifier, runbooks=RUNBOOKS)

        assert argv_calls == [
            ["docker", "exec", "oncall-demo-mysql-1", "mysql", "-uroot", "-e", "KILL 42"]
        ]
