"""T2 验收：规则通道谓词注册表（G2 定案）。

钉死契约：
- 每条规则正反例：命中 → false_positive（带 reason）；不命中 → pass（交 LLM 通道）
- 注册表编排：依序执行、首条命中即短路直判；全部 pass → 返回 None（交 LLM）
- 规则命中行 0 次 LLM 调用：MockLLMClassifier.calls 计数断言
- ClassificationResult 取值约定：confidence=1.0 / tokens=0 / cost_cny=0 /
  model="rule"（规则通道无模型，规则名经 reason 前缀归因，见 registry.py 理由）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from oncall.classify import ClassificationChannel, MockLLMClassifier, Verdict
from oncall.classify.rules import (
    DEFAULT_RULES,
    MAINTENANCE_WINDOW_RULE,
    RESOLVED_ONLY_GHOST_RULE,
    STALE_REPLAY_RULE,
    MaintenanceWindow,
    Rule,
    RuleContext,
    run_rule_channel,
)
from oncall.db import AlertEvent

NOW = datetime(2026, 9, 7, 3, 0, 0, tzinfo=UTC)
FIRED = datetime(2026, 9, 7, 2, 50, 0, tzinfo=UTC)
RESOLVED = datetime(2026, 9, 7, 2, 55, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 9, 7, 2, 45, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 9, 7, 2, 58, 0, tzinfo=UTC)

CTX = RuleContext(now=NOW)


def _event(
    *,
    labels: dict[str, str] | None = None,
    annotations: dict[str, Any] | None = None,
    fired_at: datetime = FIRED,
    resolved_at: datetime | None = None,
    last_fired_at: datetime | None = None,
) -> AlertEvent:
    """构造 alert_events 行（ORM 实例，不落库；fired_at 经 validates 回填 last_fired_at）。"""
    row = AlertEvent(
        fingerprint="a" * 64,
        source="alertmanager",
        labels_json=labels
        if labels is not None
        else {
            "alertname": "DemoApiGwHighLatency",
            "job": "api-gw",
            "instance": "api-gw-1",
            "severity": "warning",
        },
        annotations_json=annotations if annotations is not None else {},
        fired_at=fired_at,
        resolved_at=resolved_at,
    )
    if last_fired_at is not None:
        row.last_fired_at = last_fired_at
    return row


def _ghost_event() -> AlertEvent:
    """M1 issue 03 实录形态：resolved 通知无对应 firing 落成的行（dedup 兜底计为新 firing）。"""
    return _event(
        annotations={"raw_alert": {"status": "resolved", "labels": {}}},
        resolved_at=RESOLVED,
    )


def _firing_event(**kwargs: Any) -> AlertEvent:
    """正常 firing 行（未恢复，规则全 pass 的基准反例）。"""
    return _event(**kwargs)


# ── 规则 ① resolved-only 幽灵通知 ──


def test_ghost_rule_hits_resolved_only_row() -> None:
    verdict = RESOLVED_ONLY_GHOST_RULE.predicate(_ghost_event(), CTX)
    assert verdict.is_false_positive
    assert verdict.reason  # 命中必须带 reason（审计与统计归因依赖）


def test_ghost_rule_passes_normal_firing() -> None:
    verdict = RESOLVED_ONLY_GHOST_RULE.predicate(_firing_event(), CTX)
    assert not verdict.is_false_positive


def test_ghost_rule_passes_when_raw_alert_missing() -> None:
    """annotations 无 raw_alert（脏数据/历史行）→ 不判误报，放行给 LLM（防规则误杀）。"""
    verdict = RESOLVED_ONLY_GHOST_RULE.predicate(_event(annotations={}), CTX)
    assert not verdict.is_false_positive


# ── 规则 ② 维护窗口/静默期 ──


def test_maintenance_rule_hits_alert_fired_in_window() -> None:
    ctx = RuleContext(now=NOW, maintenance_windows=(MaintenanceWindow(WINDOW_START, WINDOW_END),))
    verdict = MAINTENANCE_WINDOW_RULE.predicate(_firing_event(), ctx)
    assert verdict.is_false_positive
    assert verdict.reason


def test_maintenance_rule_respects_label_selector() -> None:
    """窗口带 labels 选择子：job 匹配才命中，其他告警放行。"""
    ctx = RuleContext(
        now=NOW,
        maintenance_windows=(MaintenanceWindow(WINDOW_START, WINDOW_END, {"job": "api-gw"}),),
    )
    assert MAINTENANCE_WINDOW_RULE.predicate(_firing_event(), ctx).is_false_positive
    other = _firing_event(labels={"alertname": "X", "job": "db", "instance": "db-1"})
    assert not MAINTENANCE_WINDOW_RULE.predicate(other, ctx).is_false_positive


def test_maintenance_rule_passes_outside_window() -> None:
    """边界钉死：窗口闭区间 [starts_at, ends_at]，端点命中、窗外放行。"""
    windows = (MaintenanceWindow(WINDOW_START, WINDOW_END),)
    for moment in (WINDOW_START, WINDOW_END):
        ctx = RuleContext(now=NOW, maintenance_windows=windows)
        assert MAINTENANCE_WINDOW_RULE.predicate(
            _firing_event(fired_at=moment), ctx
        ).is_false_positive
    before = _firing_event(fired_at=datetime(2026, 9, 7, 2, 44, 59, tzinfo=UTC))
    after = _firing_event(fired_at=datetime(2026, 9, 7, 2, 58, 1, tzinfo=UTC))
    for row in (before, after):
        assert not MAINTENANCE_WINDOW_RULE.predicate(row, CTX).is_false_positive


def test_maintenance_rule_passes_without_windows() -> None:
    assert not MAINTENANCE_WINDOW_RULE.predicate(_firing_event(), CTX).is_false_positive


# ── 规则 ③ 重放/迟到期已失效 ──


def test_stale_rule_hits_resolved_and_expired() -> None:
    """endsAt（resolved_at）早于当前且已 resolved → 失效告警直判误报。"""
    verdict = STALE_REPLAY_RULE.predicate(_firing_event(resolved_at=RESOLVED), CTX)
    assert verdict.is_false_positive
    assert verdict.reason


def test_stale_rule_passes_unresolved() -> None:
    """未恢复（resolved_at=None）→ 放行：活跃告警绝不入此规则。"""
    assert not STALE_REPLAY_RULE.predicate(_firing_event(), CTX).is_false_positive


def test_stale_rule_boundary_resolved_exactly_now_passes() -> None:
    """边界钉死：resolved_at 恰等于 now 不算失效（严格早于才命中）。"""
    verdict = STALE_REPLAY_RULE.predicate(_firing_event(resolved_at=NOW), CTX)
    assert not verdict.is_false_positive


# ── 注册表编排 ──


def test_registry_short_circuits_on_first_hit() -> None:
    """首条命中即短路直判：后位规则不被执行（幽灵行同时满足规则③，归因仍归规则①）。"""
    result = run_rule_channel(_ghost_event(), CTX)
    assert result is not None
    assert result.reason.startswith("[resolved_only_ghost]")
    spy_calls: list[str] = []

    def _spy(event: AlertEvent, context: RuleContext) -> Any:
        spy_calls.append("called")
        raise AssertionError("短路失败：首条命中后不应继续执行后位规则")

    registry = (RESOLVED_ONLY_GHOST_RULE, Rule(name="spy", predicate=_spy))
    result = run_rule_channel(_ghost_event(), CTX, rules=registry)
    assert result is not None and not spy_calls


def test_registry_all_pass_returns_none_for_llm_channel() -> None:
    """全部 pass → None：这是「交 LLM 通道」的编排契约（调用方只在 None 时触达 LLM）。"""
    assert run_rule_channel(_firing_event(), CTX) is None


def test_registry_default_rule_order() -> None:
    """默认注册表依序 = 幽灵 → 维护窗口 → 重放失效（稳定性顺序，统计归因依赖）。"""
    assert [rule.name for rule in DEFAULT_RULES] == [
        "resolved_only_ghost",
        "maintenance_window",
        "stale_replay",
    ]


# ── 规则命中 → ClassificationResult（取值约定钉死）──


def test_rule_hit_maps_to_classification_result() -> None:
    result = run_rule_channel(_ghost_event(), CTX)
    assert result is not None
    assert result.verdict is Verdict.FALSE_POSITIVE
    assert result.channel is ClassificationChannel.RULE
    assert result.confidence == 1.0
    assert result.tokens == 0
    assert result.cost_cny == 0.0
    assert result.model == "rule"  # 约定：model 固定 "rule"，规则名经 reason 前缀归因
    assert result.classified_at == NOW  # 时间锚取 context.now（可注入、确定性）
    assert "[resolved_only_ghost]" in result.reason


def test_maintenance_hit_reason_prefix_carries_rule_name() -> None:
    ctx = RuleContext(now=NOW, maintenance_windows=(MaintenanceWindow(WINDOW_START, WINDOW_END),))
    result = run_rule_channel(_firing_event(), ctx)
    assert result is not None
    assert result.reason.startswith("[maintenance_window]")


# ── 规则命中行 0 次 LLM 调用 ──


def test_rule_hit_never_touches_llm_seam() -> None:
    """规则命中行 0 次 LLM 调用：命中即直判返回，LLM 接缝（MockLLMClassifier）零触达。"""
    mock = MockLLMClassifier()
    hit_rows = [
        _ghost_event(),
        _firing_event(resolved_at=RESOLVED),
        _firing_event(),
    ]
    ctx = RuleContext(now=NOW, maintenance_windows=(MaintenanceWindow(WINDOW_START, WINDOW_END),))
    contexts = [ctx, CTX, CTX]
    for row, context in zip(hit_rows, contexts, strict=True):
        result = run_rule_channel(row, context)
        if result is not None:
            assert result.channel is ClassificationChannel.RULE
    # 全流程无任何 LLM 调用；规则全 pass 的行（第 3 条）返回 None，LLM 只会在此后被调用
    assert mock.calls == []
