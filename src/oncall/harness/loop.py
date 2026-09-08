"""主循环 Loop（架构 §3.1，D-27/D-28 定案；Loop 只编排不判断）。

每步：Planner 决策 → 重复检测 → PermissionGate → 工具执行 → 步进即写落库（D-33）→
规则校验 → 假设流转 → 预算检查；组件全部构造注入（G8 判据 1）；假设文本取自
决策输出 thought（D-22 协议唯一 prose 字段，协议冻结）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from oncall.db.evidence_repo import persist_hypothesis, persist_step
from oncall.harness.context_manager import build_system_prompt, summarize_step, visible_hypotheses
from oncall.harness.permission import PermissionDecision
from oncall.harness.planner import (
    PlannerClient,
    PlannerDecision,
    PlannerOutputError,
    PlannerTimeoutError,
)
from oncall.harness.session import (
    EvidenceStep,
    Hypothesis,
    HypothesisStatus,
    InvestigationSession,
    SessionStatus,
)
from oncall.harness.tools.registry import ToolParamError, ToolRegistry, UnknownToolError
from oncall.harness.verifier import Verifier, run_rule_checks

MAX_STEPS = 15  # 架构 §3.4：步数硬安全阀
TOTAL_DURATION_LIMIT_SECONDS = 300.0  # 架构 §3.4：总时长 >5min 熔断
PREMATURE_STOP_STEP_THRESHOLD = 5  # D-28：步数 <5 且无 confirmed 即收束 → 预标注
PLANNER_RETRY_LIMIT = 2  # D-22：畸形输出重试 ≤2
CONSECUTIVE_REPEAT_LIMIT = 2  # D-27①：连续 ≥2 触发警告
TOTAL_REPEAT_LIMIT = 3  # D-27①：累计 ≥3 触发警告

_LOOP_WARNING = "循环警告：{tool} 同参重复调用（连续/累计超限），再犯将熔断转人工，请换向。"
_FALLBACK_TOOL_ERROR = "工具 {tool} 重试耗尽，结构化失败摘要：{reason}。请依据失败信息自行换向。"
_FALLBACK_PERMISSION = "工具 {tool} 被权限闸门拒绝（{decision}）；已留审计，请改用只读工具换向。"
_FALLBACK_REJECTED = "假设「{text}」已被裁决推翻（{reason}）；请依据失败摘要自行换向。"
_DEDUP_NOTICE = "假设「{text}」与既有假设相似，已拒绝入池；请换向提出差异化假设。"


class FailureMode:
    TOOL_ERROR = "tool_error"  # D-28 六值（机械可判，M7 矩阵列直接消费）
    PLAN_ERROR = "plan_error"
    TIMEOUT = "timeout"
    HALLUCINATION = "hallucination"
    NO_SIGNAL = "no_signal"
    PREMATURE_STOP = "premature_stop"


@dataclass(frozen=True)
class LoopComponents:
    """组件注入面：推理与权限分离在不同代码路径（架构 §1 总原则）。"""

    planner: PlannerClient
    registry: ToolRegistry
    gate: Any  # PermissionGate
    verifier: Verifier
    now: Callable[[], datetime]
    evidence: Any = None  # EvidenceRepository（D-33 步进即写接缝；None 不写库）


@dataclass
class InvestigationResult:
    """收尾结构（D-28）：结论/终态/六值归类/步数/成本/假设终态一并在列。"""

    incident_id: int
    status: SessionStatus
    conclusion: str | None
    failure_mode: str | None
    step_count: int
    total_tokens: int
    total_cost_cny: float
    stop_reason: str | None
    steps: list[EvidenceStep] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)


@dataclass
class _LoopState:
    """循环过程状态（C8：计数器封装进类，不落模块级）。"""

    started_at: datetime
    notices: list[str] = field(default_factory=list)
    totals: dict[tuple[str, str], int] = field(default_factory=dict)
    last_key: tuple[str, str] | None = None
    consecutive: int = 0
    pending_break_key: tuple[str, str] | None = None
    pending_tool_error: bool = False


def canonical_args(args: Mapping[str, Any]) -> str:
    """args 规范化哈希输入（D-27①）：排序后序列化，纯函数。"""
    return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


def normalize_hypothesis_text(text: str) -> str:
    """假设文本规范化（D-27②）：去空白与标点后小写，纯规则匹配。"""
    return "".join(ch for ch in text if ch.isalnum()).lower()


def _escalate(session: InvestigationSession, mode: str, reason: str) -> InvestigationResult:
    """escalate 收尾（D-28：转人工不是丢弃，证据链留在 session 可查）。"""
    session.escalate(reason)
    return _build_result(session, mode)


def run_investigation(
    session: InvestigationSession, components: LoopComponents, *, max_steps: int = MAX_STEPS
) -> InvestigationResult:
    """主循环入口：推进 step 至终止三出口之一，返回收尾结构（架构 §3.1 骨架）。"""
    state = _LoopState(started_at=components.now())
    while True:
        if session.step_count >= max_steps:  # 步数闸（MAX_STEPS 不进 prompt，§3.4）
            mode = FailureMode.TOOL_ERROR if state.pending_tool_error else None
            return _escalate(session, mode, f"步数达上限 {max_steps}，转人工")
        elapsed = (components.now() - state.started_at).total_seconds()
        if elapsed > TOTAL_DURATION_LIMIT_SECONDS:  # 时长闸
            reason = f"总时长 {elapsed:.0f}s 超限（上限 300s），熔断转人工"
            return _escalate(session, FailureMode.TIMEOUT, reason)
        decided = _decide_with_retry(session, components, state)
        if isinstance(decided, InvestigationResult):
            return decided
        if decided.conclusion is not None:  # 出口①
            return _handle_conclusion(session, components, state, decided)
        finished = _execute_step(session, components, state, decided)
        if finished is not None:
            return finished


def _decide_with_retry(
    session: InvestigationSession, components: LoopComponents, state: _LoopState
) -> PlannerDecision | InvestigationResult:
    """取决策：畸形重试 ≤2 耗尽归 plan_error；超时不重试归 timeout（D-22）。"""
    view = {
        "system_prompt": build_system_prompt(),
        "steps": [summarize_step(step) for step in session.steps],
        "hypotheses": [h.text for h in visible_hypotheses(session)],  # D-27③ 复用 T4
        "notices": list(state.notices),
    }
    for _ in range(PLANNER_RETRY_LIMIT + 1):
        try:
            return components.planner.decide(view)
        except PlannerTimeoutError as exc:
            return _escalate(session, FailureMode.TIMEOUT, f"Planner 调用超时：{exc}")
        except PlannerOutputError as exc:
            state.notices.append(f"决策输出畸形（{exc}），请严格按输出协议重试")
    return _escalate(session, FailureMode.PLAN_ERROR, "Planner 畸形输出重试耗尽，转人工")


def _track_repeat(
    session: InvestigationSession, state: _LoopState, key: tuple[str, str], tool: str
) -> InvestigationResult | None:
    """D-27①：连续 ≥2 或累计 ≥3 → 警告进下轮 prompt；紧随的再犯熔断 plan_error。"""
    state.totals[key] = state.totals.get(key, 0) + 1
    state.consecutive = state.consecutive + 1 if key == state.last_key else 1
    state.last_key = key
    hit = state.consecutive >= CONSECUTIVE_REPEAT_LIMIT or state.totals[key] >= TOTAL_REPEAT_LIMIT
    if not hit:
        return None
    if state.pending_break_key == key:
        reason = f"绕圈再犯（{tool} 同参重复调用且已警告），熔断转人工"
        return _escalate(session, FailureMode.PLAN_ERROR, reason)
    state.pending_break_key = key
    state.notices.append(_LOOP_WARNING.format(tool=tool))
    return None


def _execute_step(
    session: InvestigationSession,
    components: LoopComponents,
    state: _LoopState,
    decision: PlannerDecision,
) -> InvestigationResult | None:
    """选工具分支：检测 → 权限 → 执行 → 落步 → 校验 → 假设流转 → Fallback。"""
    tool = decision.next_tool or ""
    tripped = _track_repeat(session, state, (tool, canonical_args(decision.args or {})), tool)
    if tripped is not None:
        return tripped
    try:
        level = components.registry.level(tool)
        verdict = components.gate.check(tool, level, dict(decision.args or {}))
        if verdict is not PermissionDecision.ALLOWED:
            state.notices.append(_FALLBACK_PERMISSION.format(tool=tool, decision=verdict.value))
            return None
        execution = components.registry.execute(
            tool, decision.args or {}, step_no=session.step_count + 1
        )
    except (UnknownToolError, ToolParamError) as exc:
        state.notices.append(_FALLBACK_TOOL_ERROR.format(tool=tool, reason=str(exc)))
        return None
    result = execution.result  # EvidenceStep 100% 记录：原始输出与摘要双存（G4）
    step = EvidenceStep(
        step_no=session.step_count + 1,
        thought=decision.thought,
        tool=result.tool,
        input_json=dict(decision.args or {}),
        output_json={"status": result.status.value, "data": result.data, "meta": result.meta},
        output_summary=execution.output_summary,
        tokens=execution.tokens,
        cost_cny=execution.cost_cny,
        latency_ms=execution.latency_ms,
        ts=execution.ts,
    )
    if not persist_step(components.evidence, session, step):
        return _build_result(session, FailureMode.TOOL_ERROR)
    findings = run_rule_checks(session, decision, result=result)
    bad = next((f for f in findings if f.check == "hallucination" and not f.ok), None)
    if bad is not None:  # D-28：规则层检出臆测引用 → 熔断归类
        session.abort(bad.detail)
        return _build_result(session, FailureMode.HALLUCINATION)
    failed = _update_hypothesis(session, components, state, decision)
    if failed is not None:
        return failed
    if execution.result.meta.get("failure_mode") == FailureMode.TOOL_ERROR:
        state.pending_tool_error = True
        reason = str(execution.result.meta.get("reason", "未知错误"))
        state.notices.append(_FALLBACK_TOOL_ERROR.format(tool=tool, reason=reason))


def _update_hypothesis(
    session: InvestigationSession,
    components: LoopComponents,
    state: _LoopState,
    decision: PlannerDecision,
) -> InvestigationResult | None:
    """假设流转（D-27②/④）：去重拒绝入池；入池后裁决，推翻即 Fallback 喂回。"""
    candidate = normalize_hypothesis_text(decision.thought)
    norms = [normalize_hypothesis_text(h.text) for h in session.hypotheses]
    if candidate and any(candidate in norm or norm in candidate for norm in norms):
        state.notices.append(_DEDUP_NOTICE.format(text=decision.thought))
        return None
    hypothesis = Hypothesis(text=decision.thought, supporting_steps=[session.step_count])
    if not persist_hypothesis(components.evidence, session, hypothesis):
        return _build_result(session, FailureMode.TOOL_ERROR)
    outcome = components.verifier.request_judgment(session, hypothesis, timing="hypothesis")
    if outcome.verdict is None:
        return None
    updated = components.verifier.apply_verdict(session, hypothesis, outcome.verdict)
    if updated.status is HypothesisStatus.REJECTED:
        notice = _FALLBACK_REJECTED.format(text=updated.text, reason=outcome.verdict.reason)
        state.notices.append(notice)
    return None


def _handle_conclusion(
    session: InvestigationSession,
    components: LoopComponents,
    state: _LoopState,
    decision: PlannerDecision,
) -> InvestigationResult:
    """出口①：规则层先拦臆测收束 → 收束裁决（时机二，D-26）→ 归类 → conclude。"""
    findings = run_rule_checks(session, decision, referenced_steps=())
    bad = next((f for f in findings if f.check == "hallucination" and not f.ok), None)
    if bad is not None:
        session.abort(bad.detail)
        return _build_result(session, FailureMode.HALLUCINATION)
    latest = next(
        (h for h in reversed(session.hypotheses) if h.status is not HypothesisStatus.REJECTED), None
    )
    if latest is not None:
        outcome = components.verifier.request_judgment(session, latest, timing="conclusion")
        if outcome.verdict is not None:
            components.verifier.apply_verdict(session, latest, outcome.verdict)
    confirmed = any(h.status is HypothesisStatus.CONFIRMED for h in session.hypotheses)
    unavailable = (step.output_json.get("status") == "unavailable" for step in session.steps)
    if session.step_count > 0 and all(unavailable) and not confirmed:  # D-28 机械判据
        mode = FailureMode.NO_SIGNAL
    elif session.step_count < PREMATURE_STOP_STEP_THRESHOLD and not confirmed:
        mode = FailureMode.PREMATURE_STOP  # 预标注，不阻断收束（M7 复核）
    else:
        mode = None
    session.conclude(decision.conclusion or "")
    return _build_result(session, mode, conclusion=decision.conclusion)


def _build_result(
    session: InvestigationSession, mode: str | None, conclusion: str | None = None
) -> InvestigationResult:
    """收尾结构：六值归类与结论/步数/成本/假设终态一并列出（D-28）。"""
    return InvestigationResult(
        incident_id=session.incident_id,
        status=session.status,
        conclusion=conclusion,
        failure_mode=mode,
        step_count=session.step_count,
        total_tokens=sum(step.tokens for step in session.steps),
        total_cost_cny=sum(step.cost_cny for step in session.steps),
        stop_reason=session.stop_reason,
        steps=list(session.steps),
        hypotheses=list(session.hypotheses),
    )
