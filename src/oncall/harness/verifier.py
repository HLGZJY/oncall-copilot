"""Verifier：规则层校验 + LLM 裁决接缝（D-26 定案，架构 §3.2 / §1）。

职责边界（架构 §3.2）：只做**校验与裁决**，不生成计划、不做换向决策（D-27④）、不做步数移出。

- 规则层：纯函数族，每步零成本确定性校验——ToolResult 状态 / args 与
  工具 schema 合法性（以 `TOOL_SPECS` 为权威，勿手抄第二份清单）/
  假设-证据步引用存在性 / hallucination 判定（收束分支：零证据收束；
  假设分支：引用不存在的 step_no）
- LLM 裁决接缝：两时机（新假设提出 / 收束判定）≤3 次/调查；
  `VerifierJudge` Protocol + `VerifierVerdict{supported, reason}` 契约
  （D-22 同款 Pydantic 开法，extra=forbid 天然拒绝 next_tool 等计划字段）；
  证伪导向独立 prompt（生成者/评判者分离，架构 §1）——默认立场是推翻假设
- 计数器归属偏差（issue 05 注记）：票面原文「计数器在 session 上」，但
  `session.py` 为冻结文件不可加字段——按派工 prompt §3 边界，实现为
  Verifier 实例内按 `id(session)` 键控的独立计数状态（C8：不落模块级 dict）
- 假设流转：组件 frozen（踩坑④），写回用 `model_copy(update=...)` 重建后
  按序替换 `session.hypotheses` 列表；引用合法性由规则层前置把关

异常族 harness 自持（C3 禁 import oncall.classify），语义逐字对齐
`planner.py` 同名先例；裁决失败与超限一致降级为规则层结论，不抛错中断
调查。零网络、零真实 LLM 调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from oncall.harness.context_manager import summarize_step
from oncall.harness.planner import PlannerDecision
from oncall.harness.session import HypothesisStatus
from oncall.harness.tools.registry import TOOL_SPECS
from oncall.harness.tools.schemas import ToolStatus

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from oncall.harness.session import Hypothesis, InvestigationSession
    from oncall.harness.tools.schemas import ToolResult

__all__ = [
    "FALSIFICATION_PROMPT_TEMPLATE",
    "JUDGE_CALL_LIMIT",
    "Finding",
    "JudgmentOutcome",
    "MockVerifierJudge",
    "Verifier",
    "VerifierError",
    "VerifierJudge",
    "VerifierOutputError",
    "VerifierTimeoutError",
    "VerifierVerdict",
    "check_args_schema",
    "check_hallucination",
    "check_step_refs",
    "check_tool_result",
    "run_rule_checks",
]

JUDGE_CALL_LIMIT = 3  # D-26：每次调查 ≤3 次 LLM 裁决

JudgeTiming = Literal["hypothesis", "conclusion"]

FALSIFICATION_PROMPT_TEMPLATE = """\
## 角色（证伪导向）
你是根因调查的独立评审员，与提出假设的调查执行体相互分离。
你的默认立场是**推翻**假设而不是证明它：只有当相关证据与假设预测一致、
且不存在与之矛盾的证据步时，才允许支持。
## 待裁决假设
{hypothesis}
## 相关证据（证据步四要素摘要，来自调查会话）
{evidence}
## 输出协议
只输出一个 JSON 对象，禁止输出任何其他文字：
{{"supported": true|false, "reason": "<一句话依据，必须引用证据>"}}
禁止输出 next_tool 或任何计划字段——裁决不产生计划（架构 §3.2）。"""


class VerifierError(Exception):
    """Verifier 裁决失败基类（调用方降级为规则层结论，不中断调查）。"""


class VerifierOutputError(VerifierError):
    """畸形输出：契约校验失败。语义对齐 `planner.PlannerOutputError`（禁 import classify）。"""


class VerifierTimeoutError(VerifierError):
    """裁决调用超时（30s 上限，不重试）。语义对齐 `planner.PlannerTimeoutError`。"""


class VerifierVerdict(BaseModel):
    """裁决输出契约（D-26）：`{supported, reason}`；extra=forbid 使计划字段在契约层即被拒。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    supported: bool
    reason: str = Field(min_length=1)


@runtime_checkable
class VerifierJudge(Protocol):
    """裁决接缝：输入证伪导向上下文，输出已校验裁决；异常即契约的一部分
    （畸形/超时表达为上列异常族，降级策略由 Verifier 统一执行）。"""

    def judge(self, context_view: Mapping[str, Any]) -> VerifierVerdict: ...


@dataclass(frozen=True)
class Finding:
    """规则层单条校验结论：`check` 取值 tool_result / args_schema / step_refs / hallucination。"""

    check: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class JudgmentOutcome:
    """裁决结果：`verdict=None` 即降级（超限 / 无 judge / 裁决异常），不产出计划字段。"""

    verdict: VerifierVerdict | None
    degraded: bool
    reason: str


class MockVerifierJudge:
    """可编程 mock：支持 / 推翻 / 超时（异常）三态夹具混排回放，供 T6 编排复用。

    照 `MockPlanner` 剧本风格：按序回放、异常项抛出、耗尽稳定回落默认裁决
    （缺省不支持——证伪导向的缺省立场）。
    """

    def __init__(
        self,
        *,
        script: Sequence[VerifierVerdict | VerifierError] | None = None,
        default: VerifierVerdict | None = None,
    ) -> None:
        self._default = default or VerifierVerdict(supported=False, reason="剧本耗尽，缺省不通过")
        self._script = list(script or [])
        self._cursor = 0
        self.calls: list[dict[str, Any]] = []

    def judge(self, context_view: Mapping[str, Any]) -> VerifierVerdict:
        """回放下一条剧本；耗尽后稳定返回默认裁决（不抛 StopIteration）。"""
        self.calls.append(dict(context_view))
        if self._cursor < len(self._script):
            item = self._script[self._cursor]
            self._cursor += 1
            if isinstance(item, VerifierError):
                raise item
            return item
        return self._default


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


def run_rule_checks(
    session: InvestigationSession,
    decision: PlannerDecision,
    *,
    result: ToolResult | None = None,
    referenced_steps: Sequence[int] = (),
) -> list[Finding]:
    """规则层组合入口：四类校验按序产出 findings（result 缺省跳过校验 1）。"""
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
    return findings


# ---------------------------------------------------------------------------
# Verifier：裁决接缝 + 假设流转
# ---------------------------------------------------------------------------


class Verifier:
    """裁决编排：计数器 ≤3 次/调查（按 id(session) 键控）、失败降级、假设流转写回。"""

    def __init__(
        self, *, judge: VerifierJudge | None = None, call_limit: int = JUDGE_CALL_LIMIT
    ) -> None:
        self._judge = judge
        self._call_limit = call_limit
        self._calls: dict[int, int] = {}

    def calls_used(self, session: InvestigationSession) -> int:
        """该会话已发生的裁决调用次数（计数器偏差实现见模块 docstring）。"""
        return self._calls.get(id(session), 0)

    def _build_context(
        self, session: InvestigationSession, hypothesis: Hypothesis, timing: JudgeTiming
    ) -> dict[str, Any]:
        refs = (*hypothesis.supporting_steps, *hypothesis.against_steps)
        by_no = {step.step_no: step for step in session.steps}
        evidence = (
            "\n".join(
                f"- 步 {no}：{summarize_step(by_no[no])}" for no in sorted(set(refs)) if no in by_no
            )
            or "n/a"
        )
        return {"timing": timing, "hypothesis": hypothesis.text, "evidence": evidence}

    def request_judgment(
        self, session: InvestigationSession, hypothesis: Hypothesis, *, timing: JudgeTiming
    ) -> JudgmentOutcome:
        """两时机裁决入口：超限 / 无 judge / 裁决异常一律降级，不抛错中断调查。"""
        if self._judge is None or self.calls_used(session) >= self._call_limit:
            return JudgmentOutcome(
                verdict=None,
                degraded=True,
                reason="裁决次数已达上限" if self._judge is not None else "未注入裁决方",
            )
        self._calls[id(session)] = self.calls_used(session) + 1
        try:
            verdict = self._judge.judge(self._build_context(session, hypothesis, timing))
        except VerifierError as exc:
            return JudgmentOutcome(verdict=None, degraded=True, reason=f"裁决失败降级规则层：{exc}")
        return JudgmentOutcome(verdict=verdict, degraded=False, reason="裁决成功")

    def apply_verdict(
        self, session: InvestigationSession, hypothesis: Hypothesis, verdict: VerifierVerdict
    ) -> Hypothesis:
        """裁决驱动假设流转：rejected / confirmed 写回 session.hypotheses。

        引用合法性由规则层校验 3 前置把关；踩坑④：frozen 对象用
        `model_copy(update=...)` 重建后按序替换列表，不改写原对象。
        """
        dangling = _dangling_refs(session, hypothesis)
        if dangling:
            msg = f"假设引用不存在的证据步：{dangling}，拒绝流转"
            raise VerifierError(msg)
        status = HypothesisStatus.CONFIRMED if verdict.supported else HypothesisStatus.REJECTED
        updated = hypothesis.model_copy(update={"status": status})
        session.hypotheses = [updated if h is hypothesis else h for h in session.hypotheses]
        return updated
