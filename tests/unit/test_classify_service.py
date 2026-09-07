"""双通道编排落库服务测试（issue 04 / G4+G5 定案，decisions.md D-19）。

钉死的行为：
- 三态落库：false_positive/risk/incident → `classification_json` 八键齐全、
  status 翻 classified；未被处理的行保持 NULL + deduped
- 规则通道先行：规则命中行直判落库，0 次 LLM 调用（llm_calls 计数）
- incidents 1:1 建档：incident → 五字段齐全（severity 取 labels.severity
  缺省 warning）；risk/false_positive 不建档
- 幂等：已 classified 行跳过，重复 classify 零副作用、llm_calls=0
- llm_error 兜底行：confidence/reason 两常量原样进审计字段
  （服务层不得吞掉或改写 channel 语义，issue 03 钉死的兜底契约）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import StaticPool, create_engine, select
from sqlalchemy.orm import Session

from oncall.classify import (
    LLMOutputError,
    LLMTimeoutError,
    LLMVerdict,
    MockLLMClassifier,
    Verdict,
)
from oncall.classify.llm import (
    FALLBACK_REASON_OUTPUT_ERROR,
    FALLBACK_REASON_TIMEOUT,
    LLMChannel,
    LLMChannelOptions,
)
from oncall.classify.service import ClassifyOptions, classify_alerts
from oncall.db import AlertEvent, Incident, create_tables

NOW = datetime(2026, 9, 7, 7, 0, 0, tzinfo=UTC)
FIRED_AT = datetime(2026, 9, 6, 6, 28, 21, tzinfo=UTC)
MODEL = "deepseek-chat"
CLASSIFICATION_KEYS = {
    "verdict",
    "confidence",
    "reason",
    "channel",
    "model",
    "tokens",
    "cost_cny",
    "classified_at",
}


def make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return engine


def make_channel(mock: MockLLMClassifier | None = None) -> LLMChannel:
    return LLMChannel(
        mock or MockLLMClassifier(),
        model=MODEL,
        samples=[],
        options=LLMChannelOptions(classified_at=NOW),
    )


def make_row(
    session: Session,
    fingerprint: str,
    *,
    labels: dict[str, Any] | None = None,
    annotations: dict[str, Any] | None = None,
    resolved_at: datetime | None = None,
) -> AlertEvent:
    row = AlertEvent(
        fingerprint=fingerprint,
        source="alertmanager",
        labels_json=labels
        or {"alertname": "DemoApiGwHighLatency", "job": "api-gw", "severity": "warning"},
        annotations_json=annotations or {},
        fired_at=FIRED_AT,
        resolved_at=resolved_at,
    )
    session.add(row)
    return row


def classify(session: Session, ids: list[int], channel: LLMChannel | None = None):
    return classify_alerts(
        session, ids, llm_channel=channel or make_channel(), options=ClassifyOptions(now=NOW)
    )


class TestThreeVerdictPersistence:
    def test_rule_hit_false_positive_persists_audit_fields_with_zero_llm_calls(self):
        """规则命中 → 直判 false_positive 落库，规则先行 0 次 LLM 调用（G4）。"""
        engine = make_engine()
        with Session(engine) as session:
            row = make_row(session, "fp-rule", annotations={"raw_alert": {"status": "resolved"}})
            session.commit()
            row_id = row.id

            summary = classify(session, [row_id], channel=make_channel())

        assert summary.classified == 1
        assert summary.false_positive == 1
        assert summary.risk == 0
        assert summary.incident == 0
        assert summary.llm_calls == 0  # 规则先行：命中行不触达 LLM

        with Session(engine) as session:
            stored = session.get(AlertEvent, row_id)
            assert stored.status == "classified"
            payload = stored.classification_json
            assert set(payload) == CLASSIFICATION_KEYS
            assert payload["verdict"] == Verdict.FALSE_POSITIVE.value
            assert payload["channel"] == "rule"
            assert payload["model"] == "rule"
            assert payload["tokens"] == 0
            assert payload["cost_cny"] == 0.0

    def test_llm_incident_and_low_confidence_risk_both_persist(self):
        """LLM 高置信 incident 与低置信派生 risk（deriver 语义）各自落库。"""
        engine = make_engine()
        mock = MockLLMClassifier(
            script=[
                LLMVerdict.model_validate(
                    {"verdict": "incident", "confidence": 0.9, "reason": "证据充分"}
                ),
                LLMVerdict.model_validate(
                    {"verdict": "incident", "confidence": 0.5, "reason": "证据模糊"}
                ),
            ]
        )
        with Session(engine) as session:
            hit = make_row(session, "fp-hit")
            fuzzy = make_row(session, "fp-fuzzy")
            session.commit()
            hit_id, fuzzy_id = hit.id, fuzzy.id

            summary = classify(session, [hit_id, fuzzy_id], channel=make_channel(mock))

        assert summary.classified == 2
        assert summary.incident == 1
        assert summary.risk == 1
        assert summary.llm_calls == 2  # 两行均进 LLM 通道

        with Session(engine) as session:
            stored_hit = session.get(AlertEvent, hit_id)
            stored_fuzzy = session.get(AlertEvent, fuzzy_id)
            assert stored_hit.status == "classified"
            assert stored_hit.classification_json["verdict"] == Verdict.INCIDENT.value
            assert stored_hit.classification_json["channel"] == "llm"
            assert stored_hit.classification_json["model"] == MODEL
            # pydantic mode="json" 对 UTC 用 Z 后缀（RFC 3339）
            assert stored_hit.classification_json["classified_at"] == "2026-09-07T07:00:00Z"
            assert stored_fuzzy.classification_json["verdict"] == Verdict.RISK.value
            assert stored_fuzzy.classification_json["confidence"] == 0.5

    def test_ids_outside_request_stay_null_and_deduped(self):
        """未进本次请求的行保持 classification_json=NULL + status=deduped。"""
        engine = make_engine()
        with Session(engine) as session:
            touched = make_row(session, "fp-touched")
            untouched = make_row(session, "fp-untouched")
            session.commit()
            touched_id, untouched_id = touched.id, untouched.id

            classify(session, [touched_id])

        with Session(engine) as session:
            stored = session.get(AlertEvent, untouched_id)
            assert stored.classification_json is None
            assert stored.status == "deduped"


class TestIncidentFiling:
    def test_incident_verdict_files_one_to_one_with_five_fields(self):
        """incident 判定 → 1:1 建档：五字段齐全，severity 取 labels 缺省 warning（G5）。"""
        engine = make_engine()
        with Session(engine) as session:
            row = make_row(session, "fp-inc")
            session.commit()
            row_id = row.id

            classify(session, [row_id])

        with Session(engine) as session:
            incidents = session.scalars(select(Incident)).all()
            assert len(incidents) == 1
            filed = incidents[0]
            assert filed.alert_ids == [row_id]  # 单元素数组（归并预留，G5）
            assert filed.severity == "warning"
            assert filed.status == "investigating"
            assert filed.created_at is not None

    def test_missing_severity_label_defaults_to_warning(self):
        engine = make_engine()
        with Session(engine) as session:
            row = make_row(session, "fp-nosev", labels={"alertname": "X", "job": "y"})
            session.commit()
            classify(session, [row.id])

        with Session(engine) as session:
            filed = session.scalars(select(Incident)).one()
            assert filed.severity == "warning"

    def test_risk_and_false_positive_do_not_file_incidents(self):
        engine = make_engine()
        mock = MockLLMClassifier(confidence=0.5)  # 低置信 → risk
        with Session(engine) as session:
            rule_row = make_row(
                session, "fp-rule", annotations={"raw_alert": {"status": "resolved"}}
            )
            risk_row = make_row(session, "fp-risk")
            session.commit()
            rule_id, risk_id = rule_row.id, risk_row.id

            classify(session, [rule_id, risk_id], channel=make_channel(mock))

        with Session(engine) as session:
            assert session.scalars(select(Incident)).all() == []


class TestIdempotency:
    def test_reclassifying_classified_rows_is_noop(self):
        """重复 classify 幂等：已 classified 行跳过，零新增分类、零 LLM 调用、零建档。"""
        engine = make_engine()
        with Session(engine) as session:
            row = make_row(session, "fp-idem")
            session.commit()
            first = classify(session, [row.id])

            second = classify(session, [row.id])

        assert first.classified == 1
        assert second.classified == 0
        assert second.llm_calls == 0
        with Session(engine) as session:
            assert session.scalars(select(Incident)).all() != []  # 不重复建档
            assert len(session.scalars(select(Incident)).all()) == 1


class TestLLMErrorFallbackPersistence:
    def test_output_error_fallback_persists_two_constants(self):
        """畸形输出耗尽重试 → risk 兜底行落库：confidence/reason 常量原样进审计字段。"""
        engine = make_engine()
        mock = MockLLMClassifier(script=[LLMOutputError()] * 3)  # 1 + 2 次重试全失败
        with Session(engine) as session:
            row = make_row(session, "fp-err")
            session.commit()
            row_id = row.id

            summary = classify(session, [row_id], channel=make_channel(mock))

        assert summary.risk == 1
        with Session(engine) as session:
            payload = session.get(AlertEvent, row_id).classification_json
            assert payload["verdict"] == Verdict.RISK.value
            assert payload["channel"] == "llm_error"
            assert payload["confidence"] == 0.0
            assert payload["reason"] == FALLBACK_REASON_OUTPUT_ERROR

    def test_timeout_fallback_persists_reason_without_retry(self):
        engine = make_engine()
        mock = MockLLMClassifier(script=[LLMTimeoutError()])
        with Session(engine) as session:
            row = make_row(session, "fp-timeout")
            session.commit()
            row_id = row.id

            classify(session, [row_id], channel=make_channel(mock))

        with Session(engine) as session:
            payload = session.get(AlertEvent, row_id).classification_json
            assert payload["channel"] == "llm_error"
            assert payload["reason"] == FALLBACK_REASON_TIMEOUT
