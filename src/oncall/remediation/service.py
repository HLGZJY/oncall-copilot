"""处置状态机 + proposal 落库服务（M5 issue 03 / T3 / G8 / D-46）。

remediation 模块的确定性核心：无 LLM、零 HTTP、零 subprocess（受控执行器归 05）。
状态集与合法迁移边在此一处钉死（模块常量），供 04 同步 confirm 链、05/06 分段驱动。

D-40「confirm 即执行」：04 在同一 confirm 调用内同步遍历
pending→approved→executing→终态；approved 是「人点头已落库」的持久标记
（decision/confirm_reason/confirmed_at 落点），同步流里即使瞬态也必须是合法态。
D-44/D-45 语义（06 参考）：恢复验证通过 → recovered；未恢复 → 执行回滚 →
rolled_back / 仍失败 → failed；回滚为空的 runbook（如 cpu-spike rollback=[]）未
恢复有 executing→escalated 直边（incident 保持 investigating，转人工不丢弃）。

时间戳口径：confirmed_at 在 approve/reject 落；executed_at 在 executing 落；
finished_at 在终态（recovered/failed/rolled_back/escalated）落。

`dry_run_json` 是批准对象（D-39）：本服务任何路径**永不写它**（验收⑤，测试钉死）。

C3 零边：本模块不 import harness（issue 02 `ProposalCreator` 是结构协议，
鸭子类型即满足——`make_proposal_creator` 返回的闭包天然对账）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from oncall.db.models import RemediationProposal

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "PROPOSAL_STATES",
    "TERMINAL_STATES",
    "ProposalStateError",
    "approve",
    "create_proposal",
    "escalate",
    "get_proposal",
    "list_by_incident",
    "make_proposal_creator",
    "mark_failed",
    "mark_recovered",
    "mark_rolled_back",
    "reject",
    "start_execution",
    "transition",
]

# CONTEXT.md「处置提案」词条状态集（D-40/D-46），照抄不擅改
PROPOSAL_STATES: Final[frozenset[str]] = frozenset(
    {
        "pending",
        "approved",
        "rejected",
        "executing",
        "recovered",
        "failed",
        "rolled_back",
        "escalated",
    }
)
TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {"recovered", "failed", "rolled_back", "escalated"}
)

# 合法迁移表（集中一处钉死，终态不可再迁移）：
# - pending → approved/rejected：人工 confirm 两个落点（D-40）
# - approved → executing：04 同步链 / 05 执行器启动（未经 approve 不得执行）
# - executing → recovered/failed/rolled_back/escalated：06 恢复验证与回滚分叉
#   （D-44/D-45；escalated 直边服务「回滚为空的 runbook 未恢复」路径）
_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "pending": frozenset({"approved", "rejected"}),
    "approved": frozenset({"executing"}),
    "executing": frozenset({"recovered", "failed", "rolled_back", "escalated"}),
    "rejected": frozenset(),
    "recovered": frozenset(),
    "failed": frozenset(),
    "rolled_back": frozenset(),
    "escalated": frozenset(),
}


class ProposalStateError(Exception):
    """非法状态迁移（终态再迁移 / 未批准直跳执行 / 越边流转）。"""


def _now() -> datetime:
    return datetime.now(UTC)


def transition(  # noqa: PLR0913 — 迁移负载字段随 D-46 落点分组（confirm 对/验证对），收敛为对象反增间接层
    session: Session,
    proposal: RemediationProposal,
    target: str,
    *,
    decision: str | None = None,
    confirm_reason: str | None = None,
    verify_result_json: dict[str, Any] | None = None,
    rollback_status: str | None = None,
) -> RemediationProposal:
    """单一校验入口：当前态 → 目标态是否合法，非法抛 ProposalStateError。

    只写 status / decision / confirm_reason / confirmed_at / executed_at /
    finished_at / verify_result_json / rollback_status；**永不写 dry_run_json**。
    """
    if target not in PROPOSAL_STATES:
        msg = f"未知处置状态 {target!r}（合法状态集 {sorted(PROPOSAL_STATES)}）"
        raise ProposalStateError(msg)
    current = proposal.status
    if target not in _TRANSITIONS[current]:
        msg = f"非法状态迁移 {current!r} → {target!r}（合法出边 {sorted(_TRANSITIONS[current])}）"
        raise ProposalStateError(msg)
    proposal.status = target
    if target in ("approved", "rejected"):
        proposal.decision = decision
        proposal.confirm_reason = confirm_reason
        proposal.confirmed_at = _now()
    elif target == "executing":
        proposal.executed_at = _now()
    elif target in TERMINAL_STATES:
        proposal.finished_at = _now()
        proposal.verify_result_json = verify_result_json
        proposal.rollback_status = rollback_status
    session.commit()
    return proposal


# ---------------------------------------------------------------------------
# 迁移方法薄封装（命名复用 CONTEXT 词）
# ---------------------------------------------------------------------------


def approve(
    session: Session, proposal: RemediationProposal, *, decision: str, reason: str | None = None
) -> RemediationProposal:
    """pending → approved：confirm approve 落点（decision/confirm_reason/confirmed_at）。"""
    return transition(session, proposal, "approved", decision=decision, confirm_reason=reason)


def reject(
    session: Session, proposal: RemediationProposal, *, decision: str, reason: str | None = None
) -> RemediationProposal:
    """pending → rejected：confirm reject 落点（终态，不自动过期重开）。"""
    return transition(session, proposal, "rejected", decision=decision, confirm_reason=reason)


def start_execution(session: Session, proposal: RemediationProposal) -> RemediationProposal:
    """approved → executing：05 执行器启动 / 04 同步链（executed_at 落点）。"""
    return transition(session, proposal, "executing")


def complete_recovered(
    session: Session, proposal: RemediationProposal, *, verify_result_json: dict[str, Any]
) -> RemediationProposal:
    """executing → recovered：恢复验证通过（D-44，验证结果随行落库）。"""
    return transition(session, proposal, "recovered", verify_result_json=verify_result_json)


def mark_failed(
    session: Session,
    proposal: RemediationProposal,
    *,
    verify_result_json: dict[str, Any] | None = None,
    rollback_status: str | None = None,
) -> RemediationProposal:
    """executing → failed：执行失败终态。"""
    return transition(
        session,
        proposal,
        "failed",
        verify_result_json=verify_result_json,
        rollback_status=rollback_status,
    )


def mark_recovered(
    session: Session,
    proposal: RemediationProposal,
    *,
    verify_result_json: dict[str, Any] | None = None,
) -> RemediationProposal:
    """executing → recovered（测试/驱动侧薄封装，verify 可后补）。"""
    return transition(session, proposal, "recovered", verify_result_json=verify_result_json)


def mark_rolled_back(
    session: Session, proposal: RemediationProposal, *, rollback_status: str | None = None
) -> RemediationProposal:
    """executing → rolled_back：回滚成功终态（D-45）。"""
    return transition(session, proposal, "rolled_back", rollback_status=rollback_status)


def escalate(
    session: Session, proposal: RemediationProposal, *, rollback_status: str | None = None
) -> RemediationProposal:
    """executing → escalated：未恢复转人工（含回滚为空的直边，incident 不动）。"""
    return transition(session, proposal, "escalated", rollback_status=rollback_status)


# 06 主语义别名：恢复验证通过即 complete_recovered
complete_recovered = complete_recovered  # noqa: PLW0127  保持显式命名集中


# ---------------------------------------------------------------------------
# create / get / list + issue 02 接缝工厂
# ---------------------------------------------------------------------------


def create_proposal(  # noqa: PLR0913 — D-46 建行字段落点齐全（session + 6 列），收敛为对象反增间接层
    session: Session,
    *,
    incident_id: int,
    runbook_slug: str,
    action_id: str,
    dry_run_json: dict[str, Any],
    params_json: dict[str, Any] | None = None,
    investigation_id: int | None = None,
) -> int:
    """落 pending 行（dry_run_json 原文锁定），返回 proposal id。

    investigation_id 默认 None（验收④：无产出调查的处置可建行）。
    """
    row = RemediationProposal(
        incident_id=incident_id,
        investigation_id=investigation_id,
        runbook_slug=runbook_slug,
        action_id=action_id,
        dry_run_json=dict(dry_run_json),
        params_json=dict(params_json) if params_json is not None else None,
    )
    session.add(row)
    session.commit()
    return row.id


def get_proposal(session: Session, proposal_id: int) -> RemediationProposal | None:
    return session.get(RemediationProposal, proposal_id)


def list_by_incident(session: Session, incident_id: int) -> list[RemediationProposal]:
    """按 incident 列处置提案（一对多，多次处置尝试；GET /remediations?incident_id= 底座）。"""
    rows = session.scalars(
        select(RemediationProposal)
        .where(RemediationProposal.incident_id == incident_id)
        .order_by(RemediationProposal.id)
    ).all()
    return list(rows)


def make_proposal_creator(
    session: Session, incident_id: int, *, investigation_id: int | None = None
) -> Callable[[dict[str, Any]], str]:
    """与 issue 02 `ProposalCreator` 接缝对齐的工厂（供 04 装配注入）。

    payload 3 键（runbook_slug/action_id/dry_run_json）落 pending 行返回 str id。
    结构协议鸭子类型：返回闭包天然满足 `harness.tools.execute.ProposalCreator`——
    remediation 不 import harness（C3 零边）。
    """

    def creator(payload: dict[str, Any]) -> str:
        return str(
            create_proposal(
                session,
                incident_id=incident_id,
                investigation_id=investigation_id,
                runbook_slug=payload["runbook_slug"],
                action_id=payload["action_id"],
                dry_run_json=payload["dry_run_json"],
            )
        )

    return creator
