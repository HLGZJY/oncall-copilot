"""T3 验收测试：处置状态机 + proposal 落库服务（m5 issue 03）。

契约来源：docs/design/m5-remediation-gates-design.md §G2/D-40（confirm 即执行、
approve 同步链）/§G8 + decisions.md D-46（第八表）/ D-40（提案不自动过期）/
D-44/D-45（06 语义参考：恢复→recovered；回滚空且未恢复→escalated 直边）。
service 是 remediation 模块确定性核心：无 LLM、零 HTTP、零 subprocess。

覆盖（对应 issue 03 验收②③④⑤）：
- 合法迁移全链通过（pending→approved→executing→recovered 及分叉/回滚路径）
- 非法迁移拒绝（pending→recovered 直跳、终态再迁移、executing→rejected）
- proposal 行字段全落：dry_run_json 原文、params_json、decision/confirm_reason、时间戳
- dry_run_json 全路径不可变（状态机不改写批准对象）
- make_proposal_creator 与 issue 02 ProposalCreator 接缝形状对账（payload 3 键 → id）
- investigation_id 可空路径（无产出调查的处置可建行）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from oncall.db import Incident, create_tables
from oncall.db.models import RemediationProposal
from oncall.remediation.service import (
    ProposalStateError,
    approve,
    create_proposal,
    escalate,
    get_proposal,
    list_by_incident,
    make_proposal_creator,
    mark_failed,
    mark_recovered,
    mark_rolled_back,
    reject,
    start_execution,
)

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)

# issue 02 定稿的 dry_run_json 形状（execute.py preview 结构）
DRY_RUN: dict[str, Any] = {
    "runbook_slug": "slow-sql",
    "action_id": "kill-lock-session",
    "action_name": "KILL 持锁会话",
    "commands": [
        {
            "step": 1,
            "action": "mysql.kill_session",
            "command": "mysql.kill_session conn_id=$conn_id",
            "impact": "KILL demo MySQL 内持锁/睡眠会话以释放表锁（幂等）",
            "runtime_params": ["conn_id"],
        }
    ],
    "impact": "KILL demo MySQL 内持锁/睡眠会话以释放表锁（幂等）",
}


@pytest.fixture()
def engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_tables(engine)
    return engine


@pytest.fixture()
def incident_id(engine):
    with Session(engine) as session:
        incident = Incident(alert_ids=["a" * 64])
        session.add(incident)
        session.commit()
        return incident.id


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _reload(engine, proposal_id: int) -> RemediationProposal:
    with Session(engine) as session:
        return session.get(RemediationProposal, proposal_id)


def _create(engine, incident_id: int, **overrides) -> int:
    fields: dict = {
        "incident_id": incident_id,
        "runbook_slug": "slow-sql",
        "action_id": "kill-lock-session",
        "dry_run_json": dict(DRY_RUN),
    }
    fields.update(overrides)
    with Session(engine) as session:
        return create_proposal(session, **fields)


# ---------------------------------------------------------------------------
# create / get / list：落库基本面（验收③④）
# ---------------------------------------------------------------------------


class TestCreateAndGet:
    def test_create_returns_id_and_row_is_pending(self, engine, incident_id):
        pid = _create(engine, incident_id)
        row = _reload(engine, pid)
        assert row is not None and row.id == pid
        assert row.status == "pending"
        assert row.incident_id == incident_id

    def test_create_with_params_and_investigation(self, engine, incident_id):
        pid = _create(
            engine,
            incident_id,
            params_json={"conn_id": 42},
        )
        row = _reload(engine, pid)
        assert row.params_json == {"conn_id": 42}

    def test_create_without_investigation_id_allowed(self, engine, incident_id):
        """验收④：investigation_id 默认 None——无产出调查的处置可建行。"""
        pid = _create(engine, incident_id)
        row = _reload(engine, pid)
        assert row.investigation_id is None

    def test_list_by_incident(self, engine, incident_id):
        first = _create(engine, incident_id)
        second = _create(engine, incident_id, action_id="restart-worker")
        with Session(engine) as session:
            rows = list_by_incident(session, incident_id)
        assert [r.id for r in rows] == [first, second]

    def test_get_missing_returns_none(self, engine):
        with Session(engine) as session:
            assert get_proposal(session, 999) is None


# ---------------------------------------------------------------------------
# 状态机：合法迁移（验收②）——迁移表定稿见 service._TRANSITIONS
# ---------------------------------------------------------------------------


class TestLegalTransitions:
    def test_full_sync_chain_to_recovered(self, engine, incident_id):
        """D-40 同步链：pending→approved→executing→recovered 一路合法。"""
        pid = _create(engine, incident_id)
        with Session(engine) as session:
            row = get_proposal(session, pid)
            approve(session, row, decision="approve", reason="人工确认")
            start_execution(session, row)
            mark_recovered(session, row)
        row = _reload(engine, pid)
        assert row.status == "recovered"
        assert row.decision == "approve"
        assert row.confirm_reason == "人工确认"
        assert row.confirmed_at is not None
        assert row.executed_at is not None
        assert row.finished_at is not None

    def test_reject_branch_is_terminal(self, engine, incident_id):
        pid = _create(engine, incident_id)
        with Session(engine) as session:
            row = get_proposal(session, pid)
            reject(session, row, decision="reject", reason="误报")
        row = _reload(engine, pid)
        assert row.status == "rejected"
        assert row.decision == "reject"
        assert row.confirmed_at is not None
        assert row.executed_at is None

    def test_failed_and_rolled_back_branches(self, engine, incident_id):
        """D-44/D-45 分叉：executing→failed / executing→rolled_back 合法。"""
        for target_fn, expected in ((mark_failed, "failed"), (mark_rolled_back, "rolled_back")):
            pid = _create(engine, incident_id)
            with Session(engine) as session:
                row = get_proposal(session, pid)
                approve(session, row, decision="approve")
                start_execution(session, row)
                target_fn(session, row)
            assert _reload(engine, pid).status == expected

    def test_escalate_from_executing(self, engine, incident_id):
        """执行后仍失败 → escalated（incident 保持 investigating，D-44）。"""
        pid = _create(engine, incident_id)
        with Session(engine) as session:
            row = get_proposal(session, pid)
            approve(session, row, decision="approve")
            start_execution(session, row)
            escalate(session, row)
        assert _reload(engine, pid).status == "escalated"

    def test_empty_rollback_escalates_directly(self, engine, incident_id):
        """D-45 直边：回滚为空的 runbook（如 cpu-spike rollback=[]）未恢复需
        有 executing→escalated 的合法边，不经 failed/rolled_back 中转。"""
        pid = _create(engine, incident_id)
        with Session(engine) as session:
            row = get_proposal(session, pid)
            approve(session, row, decision="approve")
            start_execution(session, row)
            escalate(session, row)
        assert _reload(engine, pid).status == "escalated"


# ---------------------------------------------------------------------------
# 状态机：非法迁移拒绝（验收②）
# ---------------------------------------------------------------------------


class TestIllegalTransitions:
    def test_pending_to_recovered_direct_jump_rejected(self, engine, incident_id):
        pid = _create(engine, incident_id)
        with Session(engine) as session:
            row = get_proposal(session, pid)
            with pytest.raises(ProposalStateError, match="pending"):
                mark_recovered(session, row)
        assert _reload(engine, pid).status == "pending"

    def test_terminal_state_cannot_move_again(self, engine, incident_id):
        pid = _create(engine, incident_id)
        with Session(engine) as session:
            row = get_proposal(session, pid)
            reject(session, row, decision="reject")
            with pytest.raises(ProposalStateError, match="rejected"):
                approve(session, row, decision="approve")
        assert _reload(engine, pid).status == "rejected"

    def test_executing_to_rejected_rejected(self, engine, incident_id):
        pid = _create(engine, incident_id)
        with Session(engine) as session:
            row = get_proposal(session, pid)
            approve(session, row, decision="approve")
            start_execution(session, row)
            with pytest.raises(ProposalStateError):
                reject(session, row, decision="reject")

    def test_pending_cannot_start_execution_directly(self, engine, incident_id):
        """未经人工 approve 不得 executing（四道闸门第二道不可绕过）。"""
        pid = _create(engine, incident_id)
        with Session(engine) as session:
            row = get_proposal(session, pid)
            with pytest.raises(ProposalStateError):
                start_execution(session, row)


# ---------------------------------------------------------------------------
# dry_run_json 全路径不可变（验收⑤）
# ---------------------------------------------------------------------------


class TestDryRunImmutability:
    def test_dry_run_json_untouched_through_full_chain(self, engine, incident_id):
        """走完整条合法链后重读行：dry_run_json deep-equal 原文（批准对象锁定）。"""
        pid = _create(engine, incident_id)
        with Session(engine) as session:
            row = get_proposal(session, pid)
            approve(session, row, decision="approve", reason="确认")
            start_execution(session, row)
            mark_recovered(session, row)
        row = _reload(engine, pid)
        assert row.dry_run_json == DRY_RUN

    def test_dry_run_json_untouched_on_reject_and_error_paths(self, engine, incident_id):
        pid = _create(engine, incident_id)
        with Session(engine) as session:
            row = get_proposal(session, pid)
            approve(session, row, decision="approve")
            start_execution(session, row)
            with pytest.raises(ProposalStateError):
                reject(session, row, decision="reject")  # 非法迁移也不碰批准对象
            escalate(session, row)
        assert _reload(engine, pid).dry_run_json == DRY_RUN


# ---------------------------------------------------------------------------
# make_proposal_creator：与 issue 02 ProposalCreator 接缝对账
# ---------------------------------------------------------------------------


class TestProposalCreatorSeam:
    def test_payload_three_keys_returns_pending_id(self, engine, incident_id):
        """接缝形状 = execute.py ProposalCreator：payload 3 键 → str id → 回读 pending 行。"""
        creator = make_proposal_creator(Session(engine), incident_id)
        pid = creator(
            {
                "runbook_slug": "cpu-spike",
                "action_id": "stop-stress-and-restore-cpuset",
                "dry_run_json": dict(DRY_RUN),
            }
        )
        assert isinstance(pid, str) and int(pid) > 0
        row = _reload(engine, int(pid))
        assert row.status == "pending"
        assert row.runbook_slug == "cpu-spike"
        assert row.action_id == "stop-stress-and-restore-cpuset"
        assert row.dry_run_json == DRY_RUN
        assert row.incident_id == incident_id

    def test_creator_returns_str_and_duck_typed_callable(self, engine, incident_id):
        """结构协议鸭子类型：可调用、dict payload → str 返回即满足 execute.ProposalCreator。"""
        creator = make_proposal_creator(Session(engine), incident_id)
        pid = creator({"runbook_slug": "s", "action_id": "a", "dry_run_json": {"commands": []}})
        assert callable(creator)
        assert isinstance(pid, str) and int(pid) > 0
