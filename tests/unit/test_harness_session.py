"""M3-01 调查会话契约测试（D-25：M3 只冻结内存契约不建表）。

字段集合与架构 §4 `evidence_steps` / `hypotheses` 冻结列精确匹配；
序列化 roundtrip 即断点恢复工件；组件不可变、容器可变分离（OpenHands 原则）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from oncall.harness.session import (
    EvidenceStep,
    Hypothesis,
    HypothesisStatus,
    InvestigationSession,
    SessionStatus,
)

# 架构 §4 冻结列（id/incident_id 为库列，内存契约不含；M4 建表时补）
EVIDENCE_STEP_FIELDS = {
    "step_no",
    "thought",
    "tool",
    "input_json",
    "output_json",
    "output_summary",
    "tokens",
    "cost_cny",
    "latency_ms",
    "ts",
}
HYPOTHESIS_FIELDS = {"text", "status", "supporting_steps", "against_steps"}


def make_step(step_no: int = 1, **overrides: object) -> EvidenceStep:
    payload: dict[str, object] = {
        "step_no": step_no,
        "thought": "队列堆积，先查消费者延迟",
        "tool": "query_metrics",
        "input_json": {"query": "queue_lag"},
        "output_json": {"values": [1.0, 2.0]},
        "output_summary": "queue_lag 持续上升",
        "tokens": 120,
        "cost_cny": 0.01,
        "latency_ms": 300,
        "ts": datetime(2026, 9, 8, 8, 0, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return EvidenceStep.model_validate(payload)


class TestEvidenceStep:
    def test_fields_exactly_match_frozen_columns(self) -> None:
        """字段集合与架构 §4 evidence_steps 冻结列精确匹配（多一字段少一字段都算漂移）。"""
        assert set(EvidenceStep.model_fields) == EVIDENCE_STEP_FIELDS

    def test_frozen(self) -> None:
        step = make_step()
        with pytest.raises(ValidationError):
            step.thought = "改不动"  # type: ignore[misc]

    def test_dict_roundtrip(self) -> None:
        step = make_step()
        assert EvidenceStep.model_validate(step.model_dump()) == step

    def test_json_roundtrip_utc_z_suffix(self) -> None:
        """pydantic mode="json" UTC 序列化为 Z 后缀（踩坑⑥），roundtrip 等值。"""
        step = make_step()
        dumped = step.model_dump_json()
        assert '"2026-09-08T08:00:00Z"' in dumped
        assert EvidenceStep.model_validate_json(dumped) == step

    def test_negative_metrics_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_step(tokens=-1)
        with pytest.raises(ValidationError):
            make_step(cost_cny=-0.1)
        with pytest.raises(ValidationError):
            make_step(step_no=0)


class TestHypothesis:
    def test_fields_exactly_match_frozen_columns(self) -> None:
        assert set(Hypothesis.model_fields) == HYPOTHESIS_FIELDS

    def test_status_enum_values_lowercase(self) -> None:
        """StrEnum 成员大写、值为小写串（M2 models.py 先例），JSON 序列化即枚举值。"""
        assert HypothesisStatus.CONFIRMED.value == "confirmed"
        assert HypothesisStatus.REJECTED.value == "rejected"
        assert HypothesisStatus.ACTIVE.value == "active"
        h = Hypothesis.model_validate({"text": "消费者处理变慢", "status": "active"})
        assert h.model_dump()["status"] == "active"

    def test_dict_roundtrip_default_steps_empty(self) -> None:
        h = Hypothesis.model_validate({"text": "DB 慢查询", "status": "confirmed"})
        assert h.supporting_steps == [] and h.against_steps == []
        assert Hypothesis.model_validate(h.model_dump()) == h

    def test_frozen(self) -> None:
        h = Hypothesis.model_validate({"text": "x", "status": "active"})
        with pytest.raises(ValidationError):
            h.status = HypothesisStatus.CONFIRMED  # type: ignore[misc]


class TestInvestigationSession:
    def test_initial_state_running(self) -> None:
        s = InvestigationSession(incident_id=1)
        assert s.status is SessionStatus.RUNNING
        assert s.step_count == 0 and s.stop_reason is None
        assert s.steps == [] and s.hypotheses == []

    def test_record_step_advances_count(self) -> None:
        s = InvestigationSession(incident_id=1)
        s.record_step(make_step(step_no=1))
        s.record_step(make_step(step_no=2))
        assert s.step_count == 2 and len(s.steps) == 2

    def test_record_step_must_be_sequential(self) -> None:
        s = InvestigationSession(incident_id=1)
        s.record_step(make_step(step_no=1))
        with pytest.raises(ValueError, match="step_no"):
            s.record_step(make_step(step_no=3))

    def test_terminal_states_with_stop_reason(self) -> None:
        s = InvestigationSession(incident_id=1)
        s.conclude("根因证实：慢 SQL")
        assert s.status is SessionStatus.CONCLUDED
        assert s.stop_reason == "根因证实：慢 SQL"

        s2 = InvestigationSession(incident_id=2)
        s2.escalate("步数达 15")
        assert s2.status is SessionStatus.ESCALATED

        s3 = InvestigationSession(incident_id=3)
        s3.abort("Harness 熔断：绕圈")
        assert s3.status is SessionStatus.ABORTED

    def test_terminal_only_from_running(self) -> None:
        s = InvestigationSession(incident_id=1)
        s.conclude("done")
        with pytest.raises(ValueError, match="running"):
            s.escalate("二次终止")

    def test_no_step_after_concluded(self) -> None:
        s = InvestigationSession(incident_id=1)
        s.conclude("done")
        with pytest.raises(ValueError, match="running"):
            s.record_step(make_step())

    def test_add_hypothesis(self) -> None:
        s = InvestigationSession(incident_id=1)
        s.record_step(make_step(step_no=1))
        h = Hypothesis.model_validate(
            {
                "text": "慢 SQL 拖垮连接池",
                "status": "confirmed",
                "supporting_steps": [1],
            }
        )
        s.add_hypothesis(h)
        assert s.hypotheses == [h]

    def test_restore_roundtrip(self) -> None:
        """序列化 roundtrip 即断点恢复工件：状态/计数/证据步/假设全量还原。"""
        s = InvestigationSession(incident_id=42)
        s.record_step(make_step(step_no=1))
        s.record_step(make_step(step_no=2, tool="search_logs"))
        s.add_hypothesis(
            Hypothesis.model_validate({"text": "h1", "status": "rejected", "against_steps": [2]})
        )
        s.escalate("步数达 15")

        restored = InvestigationSession.model_validate_json(s.model_dump_json())
        assert restored == s
        assert restored.status is SessionStatus.ESCALATED
        assert restored.step_count == 2
        assert restored.steps[1].tool == "search_logs"
