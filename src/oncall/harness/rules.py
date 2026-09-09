"""Verifier 规则层（纯函数族）：五类确定性校验（M6-T5 起含 kb 证据防线②）。

自 verifier.py 抽出（C6 行数预算，深模块=小接口+大实现）；`_dangling_refs`
同时被 verifier.apply_verdict 使用（同包 import 合法）。零 LLM、零网络。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from oncall.harness.planner import PlannerDecision
from oncall.harness.session import HypothesisStatus
from oncall.harness.tools.registry import TOOL_SPECS
from oncall.harness.tools.schemas import ToolResult, ToolStatus

if TYPE_CHECKING:
    from oncall.harness.session import Hypothesis, InvestigationSession


@dataclass(frozen=True)
class Finding:
    """规则层单条校验结论：`check` 取值 tool_result / args_schema / step_refs /
    hallucination / kb_evidence_support（M6-T5 增）。"""

    check: str
    ok: bool
    detail: str


__all__ = [
    "Finding",
    "_dangling_refs",
    "check_args_schema",
    "check_hallucination",
    "check_kb_evidence_support",
    "check_step_refs",
    "check_tool_result",
    "run_rule_checks",
]


# ---------------------------------------------------------------------------
# 规则层（纯函数族）
# ---------------------------------------------------------------------------


def check_tool_result(result: ToolResult) -> Finding:
    """校验 1：ok/empty 放行，error/unavailable 产出 finding（语义对齐 D-23/D-16）。"""
    ok = result.status in (ToolStatus.OK, ToolStatus.EMPTY)
    detail = "状态放行" if ok else f"工具 {result.tool} 状态 {result.status.value} 需调查方关注"
    return Finding(check="tool_result", ok=ok, detail=detail)


def check_args_schema(decision: PlannerDecision) -> Finding:
    """校验 2：`next_tool` 入参以 TOOL_SPECS 的 schema 为权威校验（勿手抄第二份）。"""
    if decision.next_tool is None:
        return Finding(check="args_schema", ok=True, detail="收束分支无工具入参")
    spec = next((s for s in TOOL_SPECS if s.name == decision.next_tool), None)
    if spec is None:
        return Finding(check="args_schema", ok=False, detail=f"未知工具名 {decision.next_tool}")
    try:
        spec.schema.model_validate(decision.args or {})
    except ValidationError as exc:
        errors = exc.error_count()
        return Finding(check="args_schema", ok=False, detail=f"入参不符 schema：{errors} 处")
    return Finding(check="args_schema", ok=True, detail="入参合法")


def _dangling_refs(session: InvestigationSession, hypothesis: Hypothesis) -> list[int]:
    known = {step.step_no for step in session.steps}
    refs = (*hypothesis.supporting_steps, *hypothesis.against_steps)
    return sorted({n for n in refs if n not in known})


def _dangling_refs(session: InvestigationSession, hypothesis: Hypothesis) -> list[int]:
    known = {step.step_no for step in session.steps}
    refs = (*hypothesis.supporting_steps, *hypothesis.against_steps)
    return sorted({n for n in refs if n not in known})


def check_step_refs(session: InvestigationSession) -> Finding:
    """校验 3：假设引用的 step_no 必须存在于 session.steps（假设提出分支把门）。"""
    dangling = sorted({n for h in session.hypotheses for n in _dangling_refs(session, h)})
    ok = not dangling
    detail = "引用全部存在" if ok else f"引用不存在的证据步：{dangling}"
    return Finding(check="step_refs", ok=ok, detail=detail)


def check_hallucination(
    session: InvestigationSession,
    *,
    conclusion: str | None = None,
    referenced_steps: Sequence[int] = (),
) -> Finding:
    """校验 4：hallucination 判定——收束分支：零证据步即下结论 = 无中生有；
    引用分支：引用的工具输出（step_no）在 session 中不存在即检出。"""
    missing = sorted({n for n in referenced_steps if n not in {s.step_no for s in session.steps}})
    if missing:
        return Finding(check="hallucination", ok=False, detail=f"引用了不存在的证据步：{missing}")
    if conclusion is not None and session.step_count == 0:
        return Finding(check="hallucination", ok=False, detail="零证据步即收束结论")
    return Finding(check="hallucination", ok=True, detail="未见臆测引用")


def check_kb_evidence_support(session: InvestigationSession) -> Finding:
    """校验 5（M6-T5 / 知识污染防线②）：kb 证据不可独立证实假设。

    confirmed 假设的全部支撑步均来自 query_kb（source=kb 参考证据）时判不
    通过——kb 召回语义是「历史相似案例（参考）」非当前事实（D-08/D-54），
    证实必须以本源证据（指标/日志/拓扑）共同支撑。
    """
    violated: list[str] = []
    steps_by_no = {step.step_no: step for step in session.steps}
    for hyp in session.hypotheses:
        if hyp.status is not HypothesisStatus.CONFIRMED or not hyp.supporting_steps:
            continue
        tools = [steps_by_no[n].tool for n in hyp.supporting_steps if n in steps_by_no]
        if tools and all(t == "query_kb" for t in tools):
            violated.append(f"「{hyp.text}」仅由 query_kb 支撑")
    ok = not violated
    detail = (
        "kb 证据均与本源证据共同支撑"
        if ok
        else "假设仅由 query_kb（kb 参考）支撑，不足证实：" + "；".join(violated)
    )
    return Finding(check="kb_evidence_support", ok=ok, detail=detail)


def run_rule_checks(
    session: InvestigationSession,
    decision: PlannerDecision,
    *,
    result: ToolResult | None = None,
    referenced_steps: Sequence[int] = (),
) -> list[Finding]:
    """规则层组合入口：五类校验按序产出 findings（result 缺省跳过校验 1）。"""
    findings: list[Finding] = []
    if result is not None:
        findings.append(check_tool_result(result))
    findings.append(check_args_schema(decision))
    findings.append(check_step_refs(session))
    findings.append(
        check_hallucination(
            session, conclusion=decision.conclusion, referenced_steps=referenced_steps
        )
    )
    findings.append(check_kb_evidence_support(session))
    return findings
