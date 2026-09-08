"""M4-05 / T5 / G8（D-37）：Planner 决策视图补事件锚点。

- `build_decision_view`：view 五键（四键原样搬入 + `opening`），键集合稳定
  （MockPlanner script 回放不炸）
- opening = D-17 卡片**精简投影**：D-37 定案键集 `{alertname, instance, job,
  severity, source, status, fired_at, last_fired_at}` + 三源 `context.status`
  摘要（context_status），**不含 items 全文**——投影而非全卡片（token 预算）
- 卡片缺字段容缺（None），键集合仍稳定
零 LLM 真实调用、零 HTTP 外呼：纯函数单测，不碰网络与库文件。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from oncall.harness.context_manager import build_decision_view
from oncall.harness.session import EvidenceStep, Hypothesis, InvestigationSession

FIRED_AT = "2026-09-06T06:28:21+00:00"

VIEW_KEYS = {"system_prompt", "steps", "hypotheses", "notices", "opening"}

# D-37 定案键集：8 字段 + 三源 context.status 摘要
OPENING_PROJECTION_KEYS = {
    "alertname",
    "instance",
    "job",
    "severity",
    "source",
    "status",
    "fired_at",
    "last_fired_at",
    "context_status",
}


def make_card(*, with_instance: bool = True) -> dict[str, Any]:
    """D-17 事件卡片夹具（形状照 build_alert_card：alert + context + generated_at）。"""
    labels: dict[str, str] = {
        "alertname": "DemoApiGwHighLatency",
        "job": "api-gw",
        "severity": "critical",
    }
    if with_instance:
        labels["instance"] = "10.0.0.3:8080"
    return {
        "alert": {
            "id": 7,
            "fingerprint": "fp-1",
            "source": "alertmanager",
            "status": "deduped",
            "labels": labels,
            "annotations": {},
            "am_fingerprint": "",
            "raw_alert": {"raw": "应该不进投影"},
            "webhook": {},
            "fired_at": FIRED_AT,
            "last_fired_at": FIRED_AT,
            "resolved_at": None,
            "dedup_count": 2,
        },
        "context": {
            "sources": [
                {
                    "source": "metrics",
                    "status": "ok",
                    "items": [{"series": "体积很大的 items 全文，不应进投影"}],
                    "meta": {},
                },
                {"source": "topology", "status": "unavailable", "items": [], "meta": {}},
                {"source": "changes", "status": "ok", "items": [], "meta": {}},
            ]
        },
        "generated_at": "2026-09-08T08:00:00Z",
    }


def make_session() -> InvestigationSession:
    """带 1 步 1 假设的最小会话：验证四键原样搬入语义。"""
    session = InvestigationSession(incident_id=1)
    session.steps.append(
        EvidenceStep(
            step_no=1,
            thought="查消费者延迟",
            tool="query_metrics",
            input_json={"component": "api-gw", "query": "queue_lag"},
            output_json={"status": "ok", "direction": "up", "meta": {}},
            output_summary="组件=api-gw｜指标=queue_lag｜异常方向=up｜时间窗=n/a",
            tokens=10,
            cost_cny=0.001,
            latency_ms=5,
            ts=datetime(2026, 9, 8, 8, 0, 0, tzinfo=UTC),
        )
    )
    session.hypotheses.append(Hypothesis(text="消费者延迟导致堆积", supporting_steps=[1]))
    return session


class TestBuildDecisionView:
    def test_view_key_set_exact_with_opening_none_default(self):
        """view 五键精确；opening 缺省 None 时键仍存在、值为 None（回放不炸前提）。"""
        view = build_decision_view(make_session(), ["通知一"])
        assert set(view) == VIEW_KEYS
        assert view["opening"] is None

    def test_view_explicit_none_opening_same_shape(self):
        view = build_decision_view(make_session(), [], None)
        assert set(view) == VIEW_KEYS
        assert view["opening"] is None

    def test_four_legacy_keys_semantics_unchanged(self):
        """四键逐字照搬不改语义：steps 是摘要、hypotheses 是 text 列表（踩坑⑤）。"""
        session = make_session()
        view = build_decision_view(session, ["通知一", "通知二"])
        assert view["steps"] == [session.steps[0].output_summary]
        assert view["hypotheses"] == ["消费者延迟导致堆积"]
        assert view["notices"] == ["通知一", "通知二"]
        assert view["notices"] is not session_notices  # 复制传入序列，不改调用方状态
        assert view["system_prompt"].startswith("你是自建服务的根因调查执行体")


session_notices = ["通知一", "通知二"]


class TestOpeningProjection:
    def test_projection_key_set_exact_d37(self):
        """opening 键集合精确 = D-37 定案 8 字段 + context_status。"""
        opening = build_decision_view(make_session(), [], make_card())["opening"]
        assert set(opening) == OPENING_PROJECTION_KEYS

    def test_projection_values_from_labels_and_alert_body(self):
        opening = build_decision_view(make_session(), [], make_card())["opening"]
        assert opening["alertname"] == "DemoApiGwHighLatency"
        assert opening["instance"] == "10.0.0.3:8080"
        assert opening["job"] == "api-gw"
        assert opening["severity"] == "critical"
        assert opening["source"] == "alertmanager"
        assert opening["status"] == "deduped"
        assert opening["fired_at"] == FIRED_AT
        assert opening["last_fired_at"] == FIRED_AT
        assert opening["context_status"] == {
            "metrics": "ok",
            "topology": "unavailable",
            "changes": "ok",
        }

    def test_projection_is_not_full_card(self):
        """投影非全卡片：context.items 全文与卡片外围键不进 view（D-37 理由）。"""
        opening = build_decision_view(make_session(), [], make_card())["opening"]
        assert "items" not in opening
        assert "generated_at" not in opening
        assert "raw_alert" not in opening
        assert "体积很大的 items 全文，不应进投影" not in str(opening)

    def test_missing_fields_tolerated_as_none(self):
        """卡片缺字段（如 instance）容缺 None，键集合仍稳定（踩坑⑦容缺口径）。"""
        opening = build_decision_view(make_session(), [], make_card(with_instance=False))["opening"]
        assert set(opening) == OPENING_PROJECTION_KEYS
        assert opening["instance"] is None
        assert opening["alertname"] == "DemoApiGwHighLatency"
