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

import json
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, ValidationError

from oncall.classify.client import LLMOutputError
from oncall.eval.golden import GoldenScenario
from oncall.eval.runner import Judger, Judgment
from oncall.harness.loop import normalize_hypothesis_text
from oncall.infra.llm_judge import judge_config_from_env

if TYPE_CHECKING:
    from oncall.harness.loop import InvestigationResult
    from oncall.infra.llm import LLMClientConfig

__all__ = [
    "JUDGE_ENV_PREFIX",
    "JUDGE_ENV_SUFFIXES",
    "JUDGE_MODEL_ENV",
    "Judge",
    "JudgeInput",
    "JudgeOutput",
    "MockJudge",
    "RealJudge",
    "judger_from_judge",
    "resolve_judge_config",
    "rule_judge",
    "two_tier_judger",
]

#: judge 独立 env 前缀（防自评）：与被评模型 `ONCALL_LLM_*`（G8）物理隔离。
#: 真实 judge 装配（key 门槛票）只能经本前缀读配置，不许回落被评模型 profile。
JUDGE_ENV_PREFIX = "ONCALL_JUDGE_LLM"

#: judge 模型名 env（真实 judge 装配面预留；本票只冻结常量，不读 env）
JUDGE_MODEL_ENV = f"{JUDGE_ENV_PREFIX}_MODEL"

#: judge env 尾词（issue 07）：与 profile `ONCALL_LLM_PROFILE_<NAME>_<尾词>` 同词汇
JUDGE_ENV_SUFFIXES = ("BASE_URL", "MODEL", "API_KEY", "TIMEOUT_SECONDS")

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


# ── M7-T7：真实 judge 装配与两级判定编排（key 门槛票兑现；防自评物理隔离）──

#: judge system prompt（契约口径与 rule_judge 同语义：结论→top1 / 方向→top3 /
#: 均未中→miss；只输出一个 JSON 对象）。A2 守卫：模块常量，不内联进 SDK 调用。
JUDGE_SYSTEM_PROMPT = (
    "你是 AIOps 排障评测判官。给定调查结论、证据摘要与黄金标注（真实根因），"
    "判定调查是否命中根因，只输出一个 JSON 对象："
    '{"verdict": "top1|top3|miss", "reason": "判定理由（中文一句话）"}。'
    "口径：结论命中根因=top1；结论未中但任一假设（排查方向）命中=top3；均未命中=miss。"
)


class _ChatJsonClient(Protocol):
    """infra judge client 收口接缝（`OpenAIJudgeClient.chat_json` 的最小契约）。"""

    def chat_json(self, system: str, user: str) -> dict[str, Any]: ...


class RealJudge:
    """LLM-as-judge 真实实现（issue 07）：infra client 注入，契约校验兜畸形。

    client 为 `OpenAIJudgeClient`（`ONCALL_JUDGE_LLM_*` env 装配，与被评模型
    profile 物理隔离防自评）；client 异常族（LLMOutputError/Timeout/Classifier）
    原样冒泡——重试/降级归编排层 `two_tier_judger`，本类不吞错。
    """

    def __init__(self, client: _ChatJsonClient) -> None:
        self._client = client
        self.calls: list[JudgeInput] = []

    def judge(self, payload: JudgeInput) -> JudgeOutput:
        self.calls.append(payload)
        user = json.dumps(
            {
                "conclusion": payload.conclusion,
                "evidence_summary": payload.evidence_summary,
                "golden_scenario": payload.golden_scenario,
                "golden_root_cause": payload.golden_root_cause,
            },
            ensure_ascii=False,
        )
        data = self._client.chat_json(JUDGE_SYSTEM_PROMPT, user)
        try:
            return JudgeOutput.model_validate(data)
        except ValidationError as exc:
            raise LLMOutputError(f"judge 输出未通过契约校验: {exc}") from exc


def resolve_judge_config(
    env: dict[str, str] | None = None,
) -> LLMClientConfig:
    """`ONCALL_JUDGE_LLM_<尾词>` → `ONCALL_LLM_<尾词>` 映射后复用 `from_env`。

    委托 `infra.llm_judge.judge_config_from_env`（映射逻辑单源在 infra 侧，
    前缀常量与被评模型 profile 物理隔离）；缺任一必需项 fail-fast
    （LLMConfigError，禁静默回退 mock）。
    """
    return judge_config_from_env(env)


def two_tier_judger(judge: Judge) -> Judger:
    """G2 两级判定编排：规则匹配全量兜底，规则未命中才交 judge（judge 异常回退）。

    judge 侧异常（限流/超时/畸形）不炸跑批：重试 ≤1 次后回退规则 miss，
    reason 留 `judge_error:` 追溯痕（禁丢弃纪律——回退可见可审计，不静默吞）。
    """

    def _judger(golden: GoldenScenario, result: InvestigationResult) -> Judgment:
        first = rule_judge(golden, result)
        if first.verdict != "miss":
            return first
        payload_judger = judger_from_judge(judge)
        for attempt in (1, 2):
            try:
                return payload_judger(golden, result)
            except LLMOutputError as exc:
                last: str = f"judge_error(attempt {attempt}): {exc}"
            except Exception as exc:
                last = f"judge_error(attempt {attempt}): {exc}"
        return Judgment(verdict="miss", judged_by="rule", reason=last)

    return _judger
