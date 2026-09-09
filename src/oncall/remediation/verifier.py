"""恢复验证器 + confirm 同步链回滚编排（M5 issue 04 接缝 + issue 06 实装 / T6 / G6+G7）。

issue 04 只定义注入接缝（结构协议）；issue 06 在同文件落真实实现与编排。

D-44 语义（判据来源是本票的灵魂）：验证判据来自 runbook verification 显式声明
（promql + condition + window_s），验证器只做机械判定——任何「从锚定告警解析
阈值」「从调查结论推导判据」的捷径都是伪权威（R3/D-18 教训对偶：判据错 =
恢复误判）。D-45：回滚只来自 runbook 显式 rollback（rollback=[] 合法），系统
不做逆操作推导；回滚与 actions 同执行面（经 issue 05 `ControlledExecutor`，
过白名单 + 留审计）。D-28：复验仍失败不是 bug 是设计路径——escalated 落点 +
incident 保持 investigating（转人工不是丢弃）。

**verify 返回 dict 形状（issue 04 落位注记「verify_result_json 字段全落」对账）**：
`{"recovered": bool, "promql": str, "condition": str, "window_s": int,
"observed": float | None, "samples": int}`；异常路径追加 `"error": str` 且
recovered=False（fail-closed：未知 runbook / condition 不可解析 / 无样本 /
PromQL 回查失败一律按未恢复，不静默放行）。observed = 窗口内最劣样本（最大
观测值，两 runbook 判据均为「越低越好」）。

**condition 机械判定形状（本票定稿）**：`<观测名> <op> (<数值> | <参考名>)`，
op ∈ {<=, >=, ==, <, >}；观测名 = promql 回查结果，参考名 = 构造期注入
`reference_values`（如 slow-sql 的 db_pool_size）；任一侧解析不了 → fail-closed。

**rollback_status 取值（本票定稿）**：`skipped`（rollback=[] 或 runbook 缺失，
未恢复直边转人工）/ `rolled_back`（回滚已执行，复验结果看 proposal.status）/
`blocked`（回滚被白名单拒绝未执行，审计留痕后转人工兜底）。

观察窗不真实等待：query_range 拉取窗口快照（替身直接返回），真实窗口采样归
issue 08。C3：本模块不 import harness；remediation → context/db/runbook 均合法。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Protocol

from oncall.context.promql import UNAVAILABLE_ERRORS, PromClient
from oncall.db.models import Incident, RemediationProposal
from oncall.remediation import service
from oncall.remediation.runbook import Runbook

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from oncall.infra.http import Fetcher

__all__ = [
    "ROLLBACK_BLOCKED",
    "ROLLBACK_ROLLED_BACK",
    "ROLLBACK_SKIPPED",
    "RecoveryVerifier",
    "RunbookRecoveryVerifier",
    "run_confirm_chain",
]

ROLLBACK_SKIPPED: Final[str] = "skipped"  # rollback=[]（D-45 合法），未恢复直边转人工
ROLLBACK_ROLLED_BACK: Final[str] = "rolled_back"  # 回滚已执行（复验结果看 proposal.status）
ROLLBACK_BLOCKED: Final[str] = "blocked"  # 回滚被白名单拒绝未执行（审计留痕 + 转人工兜底）

# condition 机械判定形状（见模块 docstring）：观测名 op (数值 | 参考名)
_CONDITION_RE: Final[re.Pattern[str]] = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*(<=|>=|==|<|>)\s*(\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_]*)$"
)
_QUERY_STEP_S: Final[int] = 15  # 观察窗采样步长（快照语义，真实采样节奏归 08）


class RecoveryVerifier(Protocol):
    """恢复验证器接缝：回查判据，返回含 `recovered` 布尔的验证结果 dict。"""

    def verify(self, dry_run_json: dict[str, Any]) -> dict[str, Any]: ...


def _compare(value: float, op: str, threshold: float) -> bool:
    return {
        "<=": value <= threshold,
        ">=": value >= threshold,
        "==": value == threshold,
        "<": value < threshold,
        ">": value > threshold,
    }[op]


def _fail(spec: Any, reason: str) -> dict[str, Any]:
    """fail-closed 未恢复结果（判据缺失/不可解析/回查失败不静默放行）。"""
    result: dict[str, Any] = {"recovered": False, "error": reason}
    if spec is not None:
        result |= {"promql": spec.promql, "condition": spec.condition, "window_s": spec.window_s}
    return result


class RunbookRecoveryVerifier:
    """真实恢复验证器（满足 `RecoveryVerifier` Protocol）。

    判据 = runbook verification 显式声明（D-44/G6）；PromQL 回查经 PromClient
    （Fetcher 注入，M3 取证同款接缝）；观察窗内**全部样本**满足判据才判恢复
    （判据需持续回落，单点回落不算）。
    """

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        base_url: str,
        timeout: float,
        runbooks: Mapping[str, Runbook],
        reference_values: Mapping[str, float] | None = None,
    ) -> None:
        self._client = PromClient(fetcher, base_url=base_url, timeout=timeout)
        self._runbooks: dict[str, Runbook] = dict(runbooks)
        self._reference_values: dict[str, float] = dict(reference_values or {})

    def verify(self, dry_run_json: dict[str, Any]) -> dict[str, Any]:
        slug = str(dry_run_json.get("runbook_slug", ""))
        runbook = self._runbooks.get(slug)
        if runbook is None:
            return _fail(
                None, f"runbook {slug!r} 不在验证器 runbook 库（判据来源缺失，fail-closed）"
            )
        spec = runbook.verification
        parsed = _CONDITION_RE.match(spec.condition.strip())
        if parsed is None or not self._resolvable_rhs(parsed.group(3)):
            return _fail(
                spec, f"condition {spec.condition!r} 不可机械解析（形状：观测名 op 数值/参考名）"
            )
        rhs_token = parsed.group(3)
        op, rhs = parsed.group(2), float(self._reference_values.get(rhs_token, rhs_token))
        now = datetime.now(UTC)
        try:
            series = self._client.query_range(
                spec.promql,
                start=now - timedelta(seconds=spec.window_s),
                end=now,
                step=f"{_QUERY_STEP_S}s",
            )
        except UNAVAILABLE_ERRORS as exc:
            return _fail(spec, f"PromQL 回查失败（fail-closed 按未恢复）: {exc}")
        values = [float(v) for item in series for _ts, v in (item.get("values") or [])]
        if not values:
            result = _fail(spec, "观察窗内无样本（无信号按未恢复，fail-closed）")
            result["samples"] = 0
            return result
        return {
            "recovered": all(_compare(v, op, rhs) for v in values),
            "promql": spec.promql,
            "condition": spec.condition,
            "window_s": spec.window_s,
            "observed": max(values),
            "samples": len(values),
        }

    def _resolvable_rhs(self, token: str) -> bool:
        return re.fullmatch(r"\d+(?:\.\d+)?", token) is not None or token in self._reference_values


def _mark_incident_mitigated(session: Session, incident_id: int) -> None:
    """恢复路径：incident 翻 mitigated（既有枚举流转，D-19/D-46；api 层不自行查表）。"""
    incident = session.get(Incident, incident_id)
    if incident is not None and incident.status == "investigating":
        incident.status = "mitigated"
        session.commit()


def run_confirm_chain(
    session: Session,
    proposal: RemediationProposal,
    *,
    executor: Any,
    verifier: RecoveryVerifier,
    runbooks: Mapping[str, Runbook],
) -> str:
    """confirm approve 同步链编排（D-40/G6/G7/D-44/D-45/D-28），返回终态名。

    api 层只调用本函数、一行不重写状态逻辑；落点裁决：service.py 已逼近 C6
    上限（278/300），编排落本模块（remediation 层内）。

    流程：受控执行 actions → 恢复验证 → 恢复 → recovered + incident 翻
    mitigated；未恢复 → runbook 显式 rollback 经同一执行器（白名单 + 审计）→
    复验：通过 → recovered（rollback_status=rolled_back）；仍失败 → escalated
    （incident 保持 investigating，D-28）。rollback=[] / runbook 缺失 → 直边
    escalated（rollback_status=skipped）；回滚被白名单全拒 → 不复验，escalated
    （rollback_status=blocked，不回滚的兜底）。
    """
    execution = executor.execute(proposal.dry_run_json)  # 批准对象原文直入（D-39）
    proposal.params_json = {**(proposal.params_json or {}), "execution": execution}
    session.commit()
    verify = verifier.verify(proposal.dry_run_json)
    if verify.get("recovered"):
        service.mark_recovered(session, proposal, verify_result_json=verify)
        _mark_incident_mitigated(session, proposal.incident_id)
        return "recovered"
    runbook = runbooks.get(str(proposal.runbook_slug))
    rollback_steps = runbook.rollback if runbook is not None else []
    if not rollback_steps:  # D-45：rollback=[] 合法，未恢复直边转人工
        service.escalate(session, proposal, rollback_status=ROLLBACK_SKIPPED)
        return "escalated"
    rollback_payload = {
        "commands": [
            {"step": i, "action": step.action, "params": dict(step.params)}
            for i, step in enumerate(rollback_steps, start=1)
        ]
    }
    rollback_result = executor.execute(rollback_payload)  # 回滚与 actions 同执行面（G7/D-45）
    proposal.params_json = {**(proposal.params_json or {}), "rollback": rollback_result}
    session.commit()
    if not rollback_result.get("executed"):  # 白名单全拒/未执行 → 不复验，兜底转人工
        service.escalate(session, proposal, rollback_status=ROLLBACK_BLOCKED)
        return "escalated"
    reverify = verifier.verify(proposal.dry_run_json)
    if reverify.get("recovered"):
        service.transition(
            session,
            proposal,
            "recovered",
            verify_result_json=reverify,
            rollback_status=ROLLBACK_ROLLED_BACK,
        )
        _mark_incident_mitigated(session, proposal.incident_id)
        return "recovered"
    service.escalate(session, proposal, rollback_status=ROLLBACK_ROLLED_BACK)
    return "escalated"
