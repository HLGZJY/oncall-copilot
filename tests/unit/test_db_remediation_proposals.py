"""T3 验收测试：remediation_proposals 第八表建表层（m5 issue 03）。

契约来源：docs/design/m5-remediation-gates-design.md §数据模型变更（第八表，G8）
+ decisions.md D-46（字段权威）/ D-30（现库加表 create_all 幂等）
/ D-19/D-25/D-31（incidents/evidence_steps/investigations 冻结面不扩列）。

覆盖（对应 issue 03 验收①③④）：
- 列集合与 D-46 逐字段一致（16 列，冻结列名不得擅改）
- create_all 二次执行幂等（D-30）
- FK 拒绝（非法 incident_id / investigation_id）
- status CHECK 拒绝非法值（八值冻结，照 ck_ 先例）
- investigation_id NULL 可建行（验收④：无产出调查的处置）
- 同一 incident 可多行（一对多，不加唯一约束——多次处置尝试）
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from oncall.db import Incident, create_tables
from oncall.db.models import RemediationProposal

# D-46 第八表（m5-remediation-gates-design.md §数据模型变更 行 1；16 列逐字段照抄）
PROPOSAL_COLUMNS = {
    "id",
    "incident_id",
    "investigation_id",
    "runbook_slug",
    "action_id",
    "status",
    "dry_run_json",
    "params_json",
    "decision",
    "confirm_reason",
    "confirmed_at",
    "executed_at",
    "verify_result_json",
    "rollback_status",
    "created_at",
    "finished_at",
}


@pytest.fixture()
def engine():
    engine = create_engine("sqlite://")

    # SQLite FK 默认不强制，连接级开启后 IntegrityError 才可断言（M4 先例）
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_tables(engine)
    return engine


def _new_incident(session: Session) -> int:
    incident = Incident(alert_ids=["a" * 64])
    session.add(incident)
    session.commit()
    return incident.id


def _make_proposal(incident_id: int, **overrides):
    """一条最小 proposal（status/dry_run_json 默认对齐 pending 初始态）。"""
    fields: dict = {
        "incident_id": incident_id,
        "runbook_slug": "slow-sql",
        "action_id": "kill-lock-session",
        "dry_run_json": {"runbook_slug": "slow-sql", "commands": []},
    }
    fields.update(overrides)
    return RemediationProposal(**fields)


class TestColumnContract:
    def test_proposal_columns_match_d46_exactly(self, engine):
        cols = {c["name"] for c in inspect(engine).get_columns("remediation_proposals")}
        assert cols == PROPOSAL_COLUMNS

    def test_incident_id_indexed_not_unique(self, engine):
        """proposal 锚 incident 一对多：有索引（GET /remediations?incident_id=）但无唯一约束。"""
        idx = inspect(engine).get_indexes("remediation_proposals")
        incident_idx = [i for i in idx if "incident_id" in i["column_names"]]
        assert incident_idx, "incident_id 应建索引供按事故查询"
        uqs = inspect(engine).get_unique_constraints("remediation_proposals")
        assert not [u for u in uqs if "incident_id" in u["column_names"]], (
            "一次事故可多次处置尝试，incident_id 不加唯一约束"
        )

    def test_table_scope_is_six_tables(self, engine):
        """M2 两表 + M4 三表 + M5 第八表；scenarios/eval_runs 不越界。"""
        assert sorted(inspect(engine).get_table_names()) == [
            "alert_events",
            "evidence_steps",
            "hypotheses",
            "incidents",
            "investigations",
            "kb_chunks",
            "remediation_proposals",
        ]

    def test_frozen_tables_untouched(self, engine):
        """D-19/D-25/D-31：只新增一个类，既有五表列集合零改动。"""
        assert {c["name"] for c in inspect(engine).get_columns("incidents")} == {
            "id",
            "alert_ids",
            "severity",
            "status",
            "created_at",
        }


class TestCreateAllIdempotent:
    def test_second_create_all_is_noop(self, engine):
        """D-30：create_all 幂等——第八表自动纳入 Base.metadata，重复执行不报错。"""
        create_tables(engine)
        assert sorted(inspect(engine).get_table_names()) == [
            "alert_events",
            "evidence_steps",
            "hypotheses",
            "incidents",
            "investigations",
            "kb_chunks",
            "remediation_proposals",
        ]
        cols = {c["name"] for c in inspect(engine).get_columns("remediation_proposals")}
        assert cols == PROPOSAL_COLUMNS


class TestInsertQuery:
    def test_pending_row_roundtrip_with_defaults(self, engine):
        """pending 初始态：status 默认、confirm/execute/finish 三时间戳 NULL。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_proposal(incident_id))
            session.commit()

            row = session.scalars(select(RemediationProposal)).one()
            assert row.incident_id == incident_id
            assert row.status == "pending"
            assert row.investigation_id is None
            assert row.params_json is None
            assert row.decision is None
            assert row.confirm_reason is None
            assert row.confirmed_at is None
            assert row.executed_at is None
            assert row.finished_at is None
            assert row.verify_result_json is None
            assert row.rollback_status is None
            assert row.created_at is not None
            assert row.dry_run_json == {"runbook_slug": "slow-sql", "commands": []}

    def test_same_incident_multiple_proposals_allowed(self, engine):
        """验收锚点：一次事故多次处置尝试——同 incident 两行不冲突（一对多）。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_proposal(incident_id))
            session.add(_make_proposal(incident_id, action_id="restart-worker"))
            session.commit()

            rows = session.scalars(select(RemediationProposal)).all()
            assert len(rows) == 2
            assert {r.incident_id for r in rows} == {incident_id}


class TestConstraints:
    def test_foreign_key_rejects_unknown_incident(self, engine):
        with Session(engine) as session:
            session.add(_make_proposal(incident_id=999))
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                session.commit()

    def test_foreign_key_rejects_unknown_investigation(self, engine):
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_proposal(incident_id, investigation_id=999))
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                session.commit()

    def test_status_check_rejects_bogus(self, engine):
        """status 八值冻结（照 ck_incidents_status 先例，脏值进不来）。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_proposal(incident_id, status="bogus"))
            with pytest.raises(IntegrityError):
                session.commit()

    def test_decision_check_rejects_bogus(self, engine):
        """decision 仅 approve/reject 或 NULL（confirm 落点，DB 层兜底）。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_proposal(incident_id, decision="maybe"))
            with pytest.raises(IntegrityError):
                session.commit()
