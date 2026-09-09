"""M6-T2 验收 · 闭环报告拼装（m6 issue 02 / D-49 读库禁虚构 + D-50 建议节门控）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.db.models import (
    AlertEvent,
    Hypothesis as HypothesisRow,
    Incident,
    Investigation,
    RemediationProposal,
)
from oncall.knowledge.report import (
    KB_SECTIONS,
    SUGGESTIONS_PLACEHOLDER,
    build_closed_loop_report,
    render_closed_loop_markdown,
)

FIRED = datetime(2026, 9, 6, 6, 28, 21, tzinfo=UTC)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_tables(engine)
    session = Session(engine)
    yield session
    session.close()


def _seed_full_scenario(session: Session) -> int:
    """mitigated 事件 + 已收尾调查 + 告警 + confirmed 假设 + 处置提案全链。"""
    alert = AlertEvent(
        fingerprint="a" * 64,
        source="alertmanager",
        labels_json={"alertname": "DemoApiGwHighLatency", "instance": "api-gw-1"},
        fired_at=FIRED,
        last_fired_at=FIRED,
        dedup_count=3,
        annotations_json={},
    )
    session.add(alert)
    session.flush()
    incident = Incident(alert_ids=[alert.id], status="mitigated")
    session.add(incident)
    session.flush()
    session.add(
        Investigation(
            incident_id=incident.id,
            status="concluded",
            stop_reason="planner_conclusion",
            conclusion="CPU 飙高导致事件循环饥饿",
            failure_mode=None,
            step_count=6,
            total_tokens=1200,
            total_cost_cny=0.05,
            opening_card_json={
                "alert": {
                    "labels": {"alertname": "DemoApiGwHighLatency", "instance": "api-gw-1"},
                    "severity": "warning",
                    "source": "alertmanager",
                    "status": "deduped",
                    "fired_at": FIRED.isoformat(),
                    "last_fired_at": FIRED.isoformat(),
                },
                "context": {"metrics": {"status": "ok"}},
            },
        )
    )
    session.add(
        HypothesisRow(
            incident_id=incident.id,
            text="api-gw 容器 CPU 被 stress 进程占满",
            status="confirmed",
            supporting_steps=[2, 3],
            against_steps=[],
        )
    )
    session.add(
        RemediationProposal(
            incident_id=incident.id,
            runbook_slug="cpu-spike",
            action_id="restore-cpuset",
            status="recovered",
            dry_run_json={"commands": []},
            decision="approve",
            rollback_status="skipped",
        )
    )
    session.commit()
    return incident.id


def test_five_sections_present_with_source_anchors(db: Session) -> None:
    """五节齐全；每节带 source 锚点且锚点指向真实库行（禁虚构的机械面）。"""
    incident_id = _seed_full_scenario(db)
    sections = build_closed_loop_report(db, incident_id)
    assert tuple(sections) == KB_SECTIONS
    assert sections["timeline"]["source"]["alert_events"] == [1]
    assert sections["root_cause"]["source"]["hypotheses"] == [1]
    assert sections["remediation"]["source"]["remediation_proposals"] == [1]
    assert sections["opening_card"]["source"]["investigations"] == [1]


def test_timeline_is_measured_data_not_fabricated(db: Session) -> None:
    """时间线 = alert_events 实测行直出（fired/last/resolved/dedup_count 逐字段）。"""
    incident_id = _seed_full_scenario(db)
    sections = build_closed_loop_report(db, incident_id)
    assert "fired=2026-09-06T06:28:21+00:00" in sections["timeline"]["text"]
    assert "dedup_count=3" in sections["timeline"]["text"]
    assert "(alert#1)" in sections["timeline"]["text"]


def test_root_cause_only_confirmed_hypotheses(db: Session) -> None:
    """根因节只落 confirmed；无 confirmed 时如实落「无已证实假设」不虚构。"""
    incident_id = _seed_full_scenario(db)
    sections = build_closed_loop_report(db, incident_id)
    assert "CPU 被 stress 进程占满" in sections["root_cause"]["text"]
    assert "支持步: 2, 3" in sections["root_cause"]["text"]

    _seed_empty_concluded(db)
    empty = build_closed_loop_report(db, 2)
    assert empty["root_cause"]["text"] == "无已证实假设"
    assert empty["root_cause"]["source"]["hypotheses"] == []


def _seed_empty_concluded(session: Session) -> None:
    """第二个 incident：有收尾调查但零假设（无源不虚构的反例面）。"""
    incident = Incident(alert_ids=[])
    session.add(incident)
    session.flush()
    session.add(Investigation(incident_id=incident.id, status="concluded", step_count=0))
    session.commit()


def test_suggestions_placeholder_when_llm_disabled(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-50：env 缺省关 → 占位文案；开落同款占位（真实 LLM 是 key 门槛票未实装）。"""
    incident_id = _seed_full_scenario(db)
    monkeypatch.delenv("ONCALL_KB_SUGGESTIONS_LLM", raising=False)
    sections = build_closed_loop_report(db, incident_id)
    assert sections["suggestions"]["text"] == SUGGESTIONS_PLACEHOLDER
    assert sections["suggestions"]["source"]["generated_by"] == "placeholder"
    monkeypatch.setenv("ONCALL_KB_SUGGESTIONS_LLM", "1")
    sections_on = build_closed_loop_report(db, incident_id)
    assert sections_on["suggestions"]["source"]["generated_by"] == "llm-gated"


def test_no_report_or_running_raises_key_error(db: Session) -> None:
    """无调查记录 / running 态 → KeyError（调用方转 404，与 M4 出口语义一致）。"""
    incident = Incident(alert_ids=[])
    db.add(incident)
    db.flush()
    with pytest.raises(KeyError):
        build_closed_loop_report(db, incident.id)
    db.add(Investigation(incident_id=incident.id, status="running"))
    db.commit()
    with pytest.raises(KeyError):
        build_closed_loop_report(db, incident.id)


def test_render_closed_loop_markdown_contains_all_sections(db: Session) -> None:
    """Markdown 渲染：五节标题齐全 + 文本直出零美化。"""
    incident_id = _seed_full_scenario(db)
    sections: dict[str, Any] = build_closed_loop_report(db, incident_id)
    markdown = render_closed_loop_markdown(sections)
    for title in ("开局卡片", "时间线", "根因", "处置", "改进建议"):
        assert f"### {title}" in markdown
    assert "proposal#1 [recovered]" in markdown
