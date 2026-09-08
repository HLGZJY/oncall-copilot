"""M4-T3 读库序列化器单测：`db.views.investigation_report_body`（issue 03）。

契约来源：decisions.md **D-35**（JSON 形状权威 = M3 `build_report` 现契约，
键集合与键名逐键一致，不做导出层键名映射）/ **D-31**（investigations 1:1
覆盖语义，读库天然取最近一次）/ 踩坑⑤⑩（StrEnum 值归一、confidence 按行内
status 重算不引内存态）/ 踩坑⑧（ORM 侧别名先例）。

覆盖：
- 键集合与 `api.investigation.REPORT_KEYS` 精确一致；steps/hypotheses 逐键
- `cost` 冻结列 → `cost_cny` 内存契约键（唯一键名映射点，D-25 不倒改内存侧）
- ts 无 tzinfo（SQLite 取回）→ UTC isoformat（as_utc 先例）
- steps 按 step_no 排序、hypotheses 按行 id（入池序）
- confidence：rejected+confirmed 为分母、active 不计、无已裁决 → 0.0
- opening_card 随行 JSON 透传（留存裁决：读路径零重建）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from oncall.api.investigation import REPORT_KEYS
from oncall.db import Incident, create_tables
from oncall.db.models import EvidenceStep as EvidenceStepRow
from oncall.db.models import Hypothesis as HypothesisRow
from oncall.db.models import Investigation
from oncall.db.views import investigation_report_body

STEP_TS = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)  # 落库后 SQLite 读回无 tzinfo


@pytest.fixture()
def engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_tables(engine)
    return engine


@pytest.fixture()
def incident_id(engine):
    with Session(engine) as session:
        incident = Incident(alert_ids=["a" * 64])
        session.add(incident)
        session.commit()
        return incident.id


def make_step_row(incident_id: int, step_no: int, **overrides: Any) -> EvidenceStepRow:
    fields: dict[str, Any] = {
        "incident_id": incident_id,
        "step_no": step_no,
        "thought": f"假设{step_no}：api-gw 延迟抬升",
        "tool": "query_metrics",
        "input_json": {"promql": "queue_lag"},
        "output_json": {"status": "success", "values": [0.83]},
        "output_summary": f"延迟 {step_no}ms",
        "tokens": 10 * step_no,
        "cost": 0.001 * step_no,  # 冻结列名 `cost`（内存契约 cost_cny）
        "latency_ms": 100 + step_no,
        "ts": STEP_TS,
    }
    fields.update(overrides)
    return EvidenceStepRow(**fields)


def make_hyp_row(incident_id: int, text: str, status: str, **overrides: Any) -> HypothesisRow:
    fields: dict[str, Any] = {
        "incident_id": incident_id,
        "text": text,
        "status": status,
        "supporting_steps": [1],
        "against_steps": [2],
    }
    fields.update(overrides)
    return HypothesisRow(**fields)


def persist(engine: Any, row: Investigation, steps: list, hyps: list) -> None:
    with Session(engine) as session:
        session.add(row)
        session.flush()
        for s in steps:
            session.add(s)
        for h in hyps:
            session.add(h)
        session.commit()


def load_report(engine: Any, incident_id: int) -> dict[str, Any]:
    """照 GET 端点同款查询路径取行，验序列化器输出（api 组装层由 API 测试守卫）。"""
    with Session(engine) as session:
        row = session.scalar(select(Investigation).where(Investigation.incident_id == incident_id))
        step_rows = list(
            session.scalars(
                select(EvidenceStepRow)
                .where(EvidenceStepRow.incident_id == incident_id)
                .order_by(EvidenceStepRow.step_no)
            )
        )
        hyp_rows = list(
            session.scalars(
                select(HypothesisRow)
                .where(HypothesisRow.incident_id == incident_id)
                .order_by(HypothesisRow.id)
            )
        )
        return investigation_report_body(row, step_rows, hyp_rows)


class TestInvestigationReportBody:
    def test_key_sets_match_frozen_contract(self, engine, incident_id):
        opening = {"alert": {"id": 1}, "context": {}, "generated_at": "2026-09-08T12:00:00+00:00"}
        row = Investigation(
            incident_id=incident_id,
            status="concluded",
            conclusion="收束",
            failure_mode=None,
            step_count=2,
            total_tokens=30,
            total_cost_cny=0.003,
            stop_reason="收束",
            opening_card_json=opening,
        )
        persist(
            engine,
            row,
            [make_step_row(incident_id, 1), make_step_row(incident_id, 2)],
            [make_hyp_row(incident_id, "假设一", "confirmed")],
        )

        report = load_report(engine, incident_id)

        assert set(report) == REPORT_KEYS  # D-35：键集合与 build_report 精确一致
        (step,) = [s for s in report["steps"] if s["step_no"] == 1]
        assert set(step) == {
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
        (hyp,) = report["hypotheses"]
        assert set(hyp) == {"text", "status", "supporting_steps", "against_steps"}

    def test_scalar_fields_and_opening_card_passthrough(self, engine, incident_id):
        opening = {"alert": {"id": 7}, "context": {"status": {}}, "generated_at": "x"}
        row = Investigation(
            incident_id=incident_id,
            status="escalated",
            conclusion=None,
            failure_mode="plan_error",
            step_count=15,
            total_tokens=150,
            total_cost_cny=0.015,
            stop_reason="步数上限",
            opening_card_json=opening,
        )
        persist(engine, row, [], [])

        report = load_report(engine, incident_id)

        assert report["incident_id"] == incident_id
        assert report["termination"] == "escalated"  # 踩坑⑤：行内小写串即现口径
        assert report["conclusion"] is None
        assert report["failure_mode"] == "plan_error"
        assert report["step_count"] == 15
        assert report["total_tokens"] == 150
        assert report["total_cost_cny"] == 0.015
        assert report["stop_reason"] == "步数上限"
        assert report["opening_card"] == opening  # 随行留存逐键透传（读路径零重建）

    def test_cost_column_maps_to_cost_cny_and_ts_normalized_utc(self, engine, incident_id):
        row = Investigation(incident_id=incident_id, status="concluded")
        persist(engine, row, [make_step_row(incident_id, 1)], [])

        (step,) = load_report(engine, incident_id)["steps"]

        assert step["cost_cny"] == 0.001  # 冻结列 `cost` → 内存契约 `cost_cny`
        assert step["ts"] == "2026-09-08T12:00:00Z"  # SQLite 无 tzinfo → UTC，pydantic `Z` 同口径

    def test_steps_sorted_by_step_no_and_hypotheses_by_insertion(self, engine, incident_id):
        row = Investigation(incident_id=incident_id, status="concluded", step_count=3)
        persist(
            engine,
            row,
            # 故意乱序落库，验 step_no 排序
            [make_step_row(incident_id, 2), make_step_row(incident_id, 1)],
            # 入池序 = 行 id 序
            [
                make_hyp_row(incident_id, "假设一", "confirmed"),
                make_hyp_row(incident_id, "假设二", "rejected"),
                make_hyp_row(incident_id, "假设三", "active"),
            ],
        )

        report = load_report(engine, incident_id)

        assert [s["step_no"] for s in report["steps"]] == [1, 2]
        assert [h["text"] for h in report["hypotheses"]] == ["假设一", "假设二", "假设三"]

    def test_confidence_is_confirmed_ratio_of_decided_rows(self, engine, incident_id):
        row = Investigation(incident_id=incident_id, status="concluded")
        persist(
            engine,
            row,
            [],
            [
                make_hyp_row(incident_id, "假设一", "confirmed"),
                make_hyp_row(incident_id, "假设二", "rejected"),
                make_hyp_row(incident_id, "假设三", "active"),  # 分母不计（踩坑⑩）
            ],
        )

        assert load_report(engine, incident_id)["confidence"] == 0.5

    def test_confidence_zero_when_no_decided_hypothesis(self, engine, incident_id):
        row = Investigation(incident_id=incident_id, status="aborted")
        persist(engine, row, [], [make_hyp_row(incident_id, "假设一", "active")])

        assert load_report(engine, incident_id)["confidence"] == 0.0

    def test_confidence_zero_with_empty_pool(self, engine, incident_id):
        row = Investigation(incident_id=incident_id, status="concluded", conclusion="零假设收束")
        persist(engine, row, [], [])

        assert load_report(engine, incident_id)["confidence"] == 0.0
