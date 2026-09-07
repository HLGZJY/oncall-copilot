"""prompt 组装（issue 03 / G3 ③ 定案）。

结构固定五段：系统角色（SRE 分诊员）→ 三态定义（含 D-07 风险语义）→
few-shot 样本（loader 产出，只来自 dev 集非验证剧本）→ 待分类事件卡片
（D-17 形状，上下文时间锚 `last_fired_at`）→ 输出协议（LLM 不直出 risk）。

Token 预算（G8：prompt 总量 ≤4k tokens）：组装层做粗估（`estimate_tokens`），
不做精细计量。截断策略（当前未触发，issue 07 实测后如超限再启用）：
1. few-shot 样本的 reason 截前 200 字符——三态判定主要靠 labels/fired_at 模式，
   root_cause 长尾信息对分诊增益有限；
2. 事件卡片 `context.metrics` 超长时只保留与 labels.alertname 同名前缀的键——
   保时间锚 last_fired_at 与告警名关联性，牺牲远端指标细节。
两步都只砍信息密度、不砍结构段（三态定义与输出协议永不截断）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from oncall.classify.llm.fewshot import FewShotSample

__all__ = ["LLMPrompt", "build_prompt", "estimate_tokens"]


@dataclass(frozen=True)
class LLMPrompt:
    """组装产物：system / user 两段（OpenAI-compatible messages 形状，issue 07 直用）。"""

    system: str
    user: str


SYSTEM_ROLE = """\
你是值班车队的 SRE 分诊员，负责对去重后的单条告警做三态分诊。
你只能依据事件卡片中的证据判定，不得臆测卡片之外的信息。"""

THREE_STATE_DEFINITIONS = """\
## 三态定义
- false_positive（误报）：确定性误报模式——维护窗口/静默期内触发、resolved-only
  幽灵通知、重放/迟到期已失效、阈值配置漂移在正常水位误触发等；
- risk（风险）：分类时无法判定的中间态，建档观察但不丢弃（0 漏报的保证）。
  你不直接输出该状态：当你的置信度低于系统阈值时，系统自动把判定派生为 risk；
- incident（真实事件）：卡片证据指向真实故障，需要进入调查流程。"""

OUTPUT_PROTOCOL = """\
## 输出协议
只输出一个 JSON 对象，禁止输出任何其他文字或代码块围栏：
{"verdict": "<false_positive|incident>", "confidence": <0.0~1.0>, "reason": "<一句话依据>"}
- verdict 只允许 "false_positive" 或 "incident"，不允许 "risk"；
- confidence 为你对本判定的置信度，∈ [0,1]；
- reason 必须引用卡片中的具体证据（labels / 时间 / 上下文）。"""

#: few-shot 演示输出的固定置信度（golden 不标注 confidence，演示值固定保 prompt 稳定）
FEW_SHOT_DEMO_CONFIDENCE = 0.9

_CARD_JSON_KWARGS = {"ensure_ascii": False, "sort_keys": True, "indent": 2}


def estimate_tokens(text: str) -> int:
    """粗估 tokens（G8 估算法口径）：中英混排按 2 字符 ≈ 1 token 折算。

    只服务成本估算与预算意识，不用于计量上报（实测口径在 issue 07）。
    """
    return (len(text) + 1) // 2


def build_prompt(alert_card: Mapping[str, Any], samples: Sequence[FewShotSample]) -> LLMPrompt:
    """组装 system/user 两段 prompt（同一输入产出逐字一致的结果，快照可审计）。"""
    system = f"{SYSTEM_ROLE}\n\n{THREE_STATE_DEFINITIONS}\n\n{OUTPUT_PROTOCOL}"

    sections: list[str] = ["## 参考示例（few-shot）"]
    for index, sample in enumerate(samples, start=1):
        sections.append(
            f"### 示例 {index}（场景: {sample.scenario}，标注: {sample.verdict}）\n"
            f"输入：\n{json.dumps(sample.alert_card, **_CARD_JSON_KWARGS)}\n"
            "输出：\n"
            + json.dumps(
                {
                    "verdict": sample.verdict,
                    "confidence": FEW_SHOT_DEMO_CONFIDENCE,
                    "reason": sample.reason,
                },
                **_CARD_JSON_KWARGS,
            )
        )
    sections.append("## 待分类事件卡片")
    sections.append(json.dumps(alert_card, **_CARD_JSON_KWARGS))
    sections.append("请按输出协议只输出一个 JSON 对象。")

    return LLMPrompt(system=system, user="\n\n".join(sections))
