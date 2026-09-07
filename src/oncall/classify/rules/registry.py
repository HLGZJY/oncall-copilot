"""规则通道谓词注册表（G2 定案）：确定性误报模式直判，其余放行交 LLM 通道。

术语与契约（CONTEXT.md「规则通道 / Rule Channel」）：
- 每条规则 = `{name, predicate(alert_event, context) -> RuleVerdict}`；
  RuleVerdict 只有两种判定——`false_positive`（带 reason，误报直判）或
  `pass`（放行，交 LLM 通道）；**规则不判真实**（防规则误杀真事件）。
- 编排：依序执行、**首条命中即短路直判**；全部 pass → 返回 None，
  None 即「交 LLM 通道」的编排契约（调用方只在 None 时触达 LLMClassifier）。
- 规则命中 → `ClassificationResult`（channel=rule）。取值约定（本票测试钉死）：
  confidence=1.0 / tokens=0 / cost_cny=0 / classified_at=context.now；
  **model 固定 "rule"**——model 字段语义是「产出结论的判定器」，规则通道无模型；
  规则名（稳定契约，统计报告按其归因）经 reason 前缀 `[name] ` 结构化落位，
  model 维度聚合时全部规则行归为一类、不按规则名碎化（成本统计只关心 LLM 行）。
- 规则化标准：能写成确定性谓词的才进规则通道；语义模糊一律 pass。
  「下游挂上游抑制」类模式不建引擎（G1），将来以谓词进 `DEFAULT_RULES`。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from oncall.classify.models import ClassificationChannel, ClassificationResult, Verdict
from oncall.db import AlertEvent

__all__ = [
    "DEFAULT_RULES",
    "MAINTENANCE_WINDOW_RULE",
    "RESOLVED_ONLY_GHOST_RULE",
    "STALE_REPLAY_RULE",
    "MaintenanceWindow",
    "Rule",
    "RuleContext",
    "RuleVerdict",
    "run_rule_channel",
]

RULE_CHANNEL_MODEL = "rule"  # model 字段约定：见模块 docstring「model 固定 rule」
RULE_CONFIDENCE = 1.0  # 确定性谓词直判：置信度满格（LLM 通道才存在置信度分级）


def as_utc(moment: datetime) -> datetime:
    """无时区信息的时间戳按 UTC 处理（SQLite 取回的 datetime 无 tzinfo）。"""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RuleVerdict:
    """单条规则的判定：误报直判（带 reason）或放行（G2：规则只判误报或放行，不判真实）。"""

    is_false_positive: bool
    reason: str = ""

    @classmethod
    def false_positive(cls, reason: str) -> RuleVerdict:
        """命中误报模式：reason 必填（审计与统计归因依赖）。"""
        return cls(is_false_positive=True, reason=reason)

    @classmethod
    def pass_to_llm(cls) -> RuleVerdict:
        """放行：不判误报，交 LLM 通道做语义分类。"""
        return cls(is_false_positive=False)


@dataclass(frozen=True, slots=True)
class MaintenanceWindow:
    """维护窗口/静默期：labels 选择子（空 dict = 全局生效）+ 闭区间时间窗。"""

    starts_at: datetime
    ends_at: datetime
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuleContext:
    """规则判定的环境上下文：当前时间（可注入保确定性）+ 生效中的静默窗口清单。"""

    now: datetime
    maintenance_windows: tuple[MaintenanceWindow, ...] = ()


@dataclass(frozen=True, slots=True)
class Rule:
    """注册表条目：name 是稳定契约（统计按规则名归因），predicate 只产 RuleVerdict。"""

    name: str
    predicate: Callable[[AlertEvent, RuleContext], RuleVerdict]


# ── 初始规则集（G2 定案 3 条；新规则以谓词追加进 DEFAULT_RULES，不建引擎）──


def _resolved_only_ghost(event: AlertEvent, _context: RuleContext) -> RuleVerdict:
    """规则① resolved-only 幽灵通知：resolved 通知无对应 firing 落成的行。

    判定信号：该行的落库来源通知本身是 resolved（M1 合并逻辑把无锚点的 resolved
    兜底计为新 firing，M1 issue 03 实录 4 条同类数据）。raw_alert 缺失（历史行/脏
    数据）→ 放行，不判误报（防规则误杀）。
    """
    raw = (event.annotations_json or {}).get("raw_alert")
    if isinstance(raw, dict) and raw.get("status") == "resolved":
        return RuleVerdict.false_positive(
            "resolved-only 幽灵通知：该行由 resolved 通知落库、无对应 firing"
            "（M1 issue 03 实录 4 条同类），恢复通知直判误报"
        )
    return RuleVerdict.pass_to_llm()


def _maintenance_window(event: AlertEvent, context: RuleContext) -> RuleVerdict:
    """规则② 维护窗口/静默期：最近一次 firing 落在生效窗口（闭区间）内。

    选择子按 labels 子集匹配（空 dict 全局生效）；判定锚点是 last_fired_at
    （告警最近一次触发的时间），而非分类时刻——静默的是「窗口内触发的告警」。
    """
    fired_at = as_utc(event.last_fired_at)
    labels = event.labels_json or {}
    for window in context.maintenance_windows:
        if not all(labels.get(key) == value for key, value in window.labels.items()):
            continue
        if as_utc(window.starts_at) <= fired_at <= as_utc(window.ends_at):
            return RuleVerdict.false_positive(
                "维护窗口/静默期内告警：最近一次 firing 落在静默窗口（闭区间）内，直判误报"
            )
    return RuleVerdict.pass_to_llm()


def _stale_replay(event: AlertEvent, context: RuleContext) -> RuleVerdict:
    """规则③ 重放/迟到期已失效：endsAt（resolved_at）严格早于当前且已 resolved。

    边界钉死：resolved_at 恰等于 now 不算失效（严格早于才命中）；未恢复
    （resolved_at=None）→ 放行——活跃告警绝不入此规则。
    """
    if event.resolved_at is not None and as_utc(event.resolved_at) < as_utc(context.now):
        return RuleVerdict.false_positive(
            "重放/迟到期已失效告警：endsAt 早于当前且已 resolved，失效告警直判误报"
        )
    return RuleVerdict.pass_to_llm()


RESOLVED_ONLY_GHOST_RULE = Rule(name="resolved_only_ghost", predicate=_resolved_only_ghost)
MAINTENANCE_WINDOW_RULE = Rule(name="maintenance_window", predicate=_maintenance_window)
STALE_REPLAY_RULE = Rule(name="stale_replay", predicate=_stale_replay)

# 默认注册表顺序（稳定契约，统计归因依赖）：更特异的幽灵通知在前，宽口径的失效在后
DEFAULT_RULES: tuple[Rule, ...] = (
    RESOLVED_ONLY_GHOST_RULE,
    MAINTENANCE_WINDOW_RULE,
    STALE_REPLAY_RULE,
)


def run_rule_channel(
    event: AlertEvent,
    context: RuleContext,
    rules: Sequence[Rule] = DEFAULT_RULES,
) -> ClassificationResult | None:
    """规则通道编排入口：依序执行注册表，首条命中即短路直判。

    命中 → `ClassificationResult`（channel=rule，取值约定见模块 docstring）；
    全部 pass → None（交 LLM 通道——调用方只在 None 时触达 LLMClassifier，
    规则命中行因此 0 次 LLM 调用，这是成本四杠杆第 2 位「规则先行」的落点）。
    """
    for rule in rules:
        verdict = rule.predicate(event, context)
        if verdict.is_false_positive:
            return ClassificationResult(
                verdict=Verdict.FALSE_POSITIVE,
                confidence=RULE_CONFIDENCE,
                reason=f"[{rule.name}] {verdict.reason}",
                channel=ClassificationChannel.RULE,
                model=RULE_CHANNEL_MODEL,
                tokens=0,
                cost_cny=0.0,
                classified_at=as_utc(context.now),
            )
    return None
