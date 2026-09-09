"""判对错两级：规则匹配层 + judge 契约 mock 冻结（m7 issue 03 / G2 + D-59）。

两级判对错（CONTEXT.md「判对错两级」/ 硬规 7）：
- **规则匹配层 `rule_judge`**：结论/假设与黄金 `root_cause` 做规范化包含判定
  （复用 `normalize_hypothesis_text` D-27②，勿重造）——结论层命中即 top1
  （根因关键词比对即此判定在结论上的应用），假设层命中即 top3，全未命中
  miss；judged_by 恒为 `rule`。直接实现 issue 02 预留的 `Judger` 接缝。
- **LLM-as-judge 契约**：输入=结论+证据摘要+黄金标注（`JudgeInput`），
  输出=`{verdict, reason}`（`JudgeOutput`，verdict 冻结 eval_runs CHECK 三值）；
  契约先冻结 mock 实现（`MockJudge`），真实 judge 是 key 门槛票（D-50 先例），
  本票零真实调用。畸形输出抛 `LLMOutputError`——异常契约与 `infra/llm.py`
  逐字对齐（D-07：mock 与真实实现抛同一异常族，编排层据此重试/降级）。
- **防自评**：judge 模型走 `ONCALL_JUDGE_LLM_*` 独立 env 前缀，与被评模型
  `ONCALL_LLM_*`（G8）物理隔离——解耦由常量契约机械保证，不许复用同一 profile。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, Field, ValidationError

from oncall.classify.client import LLMOutputError
from oncall.eval.golden import GoldenScenario
from oncall.eval.runner import Judger, Judgment
from oncall.harness.loop import normalize_hypothesis_text

if TYPE_CHECKING:
    from oncall.harness.loop import InvestigationResult

__all__ = [
    "JUDGE_ENV_PREFIX",
    "JUDGE_MODEL_ENV",
    "Judge",
    "JudgeInput",
    "JudgeOutput",
    "MockJudge",
    "judger_from_judge",
    "rule_judge",
]

#: judge 独立 env 前缀（防自评）：与被评模型 `ONCALL_LLM_*`（G8）物理隔离。
#: 真实 judge 装配（key 门槛票）只能经本前缀读配置，不许回落被评模型 profile。
JUDGE_ENV_PREFIX = "ONCALL_JUDGE_LLM"

#: judge 模型名 env（真实 judge 装配面预留；本票只冻结常量，不读 env）
JUDGE_MODEL_ENV = f"{JUDGE_ENV_PREFIX}_MODEL"

#: 判定三值（eval_runs CHECK 词汇，D-59；冻结在契约层防越界产出）
VERDICTS = ("top1", "top3", "miss")


def rule_judge(golden: GoldenScenario, result: InvestigationResult) -> Judgment:
    """规则匹配层（G2 一级）：规范化包含判定，命中即 top1/top3。

    判定基准 = golden `root_cause` 规范化串（「正确结论必然包含」准绳，
    与 golden_support D-29 判分同语义，规则层不直接 import 先例模块）：
    - 结论含根因 → top1（根因关键词比对：规范化后包含即命中）；
    - 结论未中但任一假设文本含根因 → top3（排查方向对、收束没对准）；
    - 全未命中 → miss。judged_by 恒为 `rule`（judged_by CHECK 词汇）。
    """
    needle = normalize_hypothesis_text(golden.root_cause)
    conclusion_norm = normalize_hypothesis_text(result.conclusion or "")
    if needle and needle in conclusion_norm:
        return Judgment(verdict="top1", judged_by="rule", reason="结论命中黄金根因（规范化包含）")
    if needle:
        for hypothesis in result.hypotheses:
            if needle in normalize_hypothesis_text(hypothesis.text):
                return Judgment(
                    verdict="top3", judged_by="rule", reason="假设命中黄金根因（结论未命中）"
                )
    return Judgment(verdict="miss", judged_by="rule", reason="结论与假设均未命中黄金根因")


class JudgeInput(BaseModel):
    """judge 契约输入（G2 冻结面）：结论 + 证据摘要 + 黄金标注。"""

    conclusion: str
    evidence_summary: str
    golden_scenario: str
    golden_root_cause: str


class JudgeOutput(BaseModel):
    """judge 契约输出（G2 冻结面）：`{verdict, reason}`，verdict 冻结三值。"""

    verdict: str = Field(pattern="^(top1|top3|miss)$")
    reason: str


@runtime_checkable
class Judge(Protocol):
    """LLM-as-judge 接缝（G2 二级）：只复核规则未命中样本（judge 与被评模型解耦）。

    异常即契约的一部分：实现方把畸形输出表达为 `LLMOutputError`
    （对齐 infra/llm.py D-07，mock 与真实实现抛同一异常族）。
    """

    def judge(self, payload: JudgeInput) -> JudgeOutput: ...


class MockJudge:
    """judge 契约冻结 mock（零真实调用）：可编程剧本回放 + 畸形输出夹具。

    `script` 按调用序消费（`JudgeOutput` / 裸 dict / `LLMOutputError` 混排）：
    裸 dict 经 `JudgeOutput` 契约校验，失败抛 `LLMOutputError`（真实 judge
    的「JSON mode + 契约校验失败」等价面，D-07 逐字对齐）；耗尽后稳定回落
    默认结论——「畸形 → 重试 → 成功」类编排测试以此为基础设施。
    """

    def __init__(
        self,
        *,
        verdict: str = "top1",
        reason: str = "mock 固定判定",
        script: list[JudgeOutput | dict[str, str] | LLMOutputError] | None = None,
    ) -> None:
        self._default = JudgeOutput.model_validate({"verdict": verdict, "reason": reason})
        self._script = list(script or [])
        self._cursor = 0
        self.calls: list[JudgeInput] = []

    def judge(self, payload: JudgeInput) -> JudgeOutput:
        """回放下一条剧本；耗尽后稳定返回默认判定（不抛 StopIteration）。"""
        self.calls.append(payload)
        if self._cursor < len(self._script):
            item = self._script[self._cursor]
            self._cursor += 1
            if isinstance(item, LLMOutputError):
                raise item
            if isinstance(item, JudgeOutput):
                return item
            try:
                return JudgeOutput.model_validate(item)
            except ValidationError as exc:
                raise LLMOutputError(f"judge 输出未通过契约校验: {exc}") from exc
        return self._default


def judger_from_judge(judge: Judge) -> Judger:
    """Judge 契约 → issue 02 `Judger` 接缝适配（judged_by 恒为 `judge`）。

    证据摘要 = 假设终态文本拼接（InvestigationResult 既有列消费不重造），
    供 judge 复核时看排查方向；判定产出经 `Judgment` 回填 eval_runs 行。
    """

    def _judger(golden: GoldenScenario, result: InvestigationResult) -> Judgment:
        payload = JudgeInput(
            conclusion=result.conclusion or "",
            evidence_summary="; ".join(h.text for h in result.hypotheses),
            golden_scenario=golden.scenario,
            golden_root_cause=golden.root_cause,
        )
        output = judge.judge(payload)
        return Judgment(verdict=output.verdict, judged_by="judge", reason=output.reason)

    return _judger
