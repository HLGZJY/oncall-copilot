"""T1 验收测试：investigations / evidence_steps / hypotheses 三表（m4 issue 01）。

契约来源：docs/architecture/architecture.md §4 冻结列（evidence_steps/hypotheses）
+ docs/design/m4-evidence-chain-design.md §数据模型变更（investigations 第七表，D-31）
+ decisions.md D-30（现库加表 create_all 幂等）/ D-31（1:1 覆盖语义）/ D-32（input 只落全文）。

覆盖：
- 三表列集合与设计文档逐字段一致（冻结列名不得擅改，D-25）
- create_all 二次执行幂等（D-30）
- FK 拒绝（非法 incident_id）+ investigations.incident_id 唯一冲突（覆盖语义锚点，D-31）
- status CHECK 拒绝（investigations 四态 / hypotheses 三态）
- (incident_id, step_no) 组合唯一
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from oncall.db import Incident, create_tables
from oncall.db.models import EvidenceStep, Hypothesis, Investigation

STEP_TS = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

# D-31 第七表（m4-evidence-chain-design.md §数据模型变更 行 1）
INVESTIGATION_COLUMNS = {
    "id",
    "incident_id",
    "status",
    "stop_reason",
    "conclusion",
    "failure_mode",
    "step_count",
    "total_tokens",
    "total_cost_cny",
    "started_at",
    "finished_at",
    # M4-T3 增列（D-35/D-31）：opening_card 随行留存（JSON 列，读路径零重建，
    # D-17 卡内 generated_at 是构建时刻时间戳——读时重建必然漂移）
    "opening_card_json",
}

# 架构 §4 冻结列（逐字段照抄，D-25：文档是权威）
EVIDENCE_STEP_COLUMNS = {
    "id",
    "incident_id",
    "step_no",
    "thought",
    "tool",
    "input_json",
    "output_json",
    "output_summary",
    "tokens",
    "cost",
    "latency_ms",
    "ts",
}

# 架构 §4 冻结列（逐字段照抄，D-25）
HYPOTHESIS_COLUMNS = {
    "id",
    "incident_id",
    "text",
    "status",
    "supporting_steps",
    "against_steps",
}


@pytest.fixture()
def engine():
    engine = create_engine("sqlite://")

    # SQLite FK 默认不强制，连接级开启后 IntegrityError 才可断言
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_tables(engine)
    return engine


def _new_incident(session: Session) -> int:
    """一条最小 incident（M2 最小集），返回其 id 作 FK 锚。"""
    incident = Incident(alert_ids=["a" * 64])
    session.add(incident)
    session.commit()
    return incident.id


def _make_investigation(incident_id: int, **overrides):
    """一条最小调查记录（status/started_at/计数默认值对齐 M3 会话初始态）。"""
    fields: dict = {"incident_id": incident_id}
    fields.update(overrides)
    return Investigation(**fields)


def _make_step(incident_id: int, step_no: int = 1, **overrides):
    """一条最小证据步（字段对齐 M3 EvidenceStep 内存契约形状）。"""
    fields: dict = {
        "incident_id": incident_id,
        "step_no": step_no,
        "thought": "api-gw 延迟抬升，先查 CPU 水位",
        "tool": "query_metrics",
        "input_json": {"query": "rate(process_cpu_seconds_total[5m])"},
        "output_json": {"status": "success", "values": [0.83]},
        "output_summary": "api-gw-1 CPU 83%",
        "tokens": 0,
        "cost": 0.0,
        "latency_ms": 120,
        "ts": STEP_TS,
    }
    fields.update(overrides)
    return EvidenceStep(**fields)


def _make_hypothesis(incident_id: int, **overrides):
    """一条最小假设（status/引用数组默认值对齐 M3 Hypothesis 内存契约）。"""
    fields: dict = {"incident_id": incident_id, "text": "CPU 饥饿导致事件循环阻塞"}
    fields.update(overrides)
    return Hypothesis(**fields)


def _aware(dt: datetime) -> datetime:
    """SQLite 取回的时间无 tzinfo，统一补 UTC 后比较（SQLite 方言不保留时区）。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class TestColumnContract:
    def test_investigations_columns_match_d31_exactly(self, engine):
        cols = {c["name"] for c in inspect(engine).get_columns("investigations")}
        assert cols == INVESTIGATION_COLUMNS

    def test_evidence_steps_columns_match_architecture_s4_exactly(self, engine):
        cols = {c["name"] for c in inspect(engine).get_columns("evidence_steps")}
        assert cols == EVIDENCE_STEP_COLUMNS

    def test_hypotheses_columns_match_architecture_s4_exactly(self, engine):
        cols = {c["name"] for c in inspect(engine).get_columns("hypotheses")}
        assert cols == HYPOTHESIS_COLUMNS

    def test_input_side_has_no_summary_column(self, engine):
        """D-32：input 只落 input_json 全文，不设 input_summary；output 侧双存照冻结。"""
        cols = {c["name"] for c in inspect(engine).get_columns("evidence_steps")}
        assert "input_summary" not in cols
        assert "output_summary" in cols

    def test_table_scope_is_six_tables(self, engine):
        """M2 两表 + M4 三表 + M5 第八表 + M6 第九表（D-55）；scenarios/eval_runs 不越界。"""
        assert sorted(inspect(engine).get_table_names()) == [
            "alert_events",
            "evidence_steps",
            "hypotheses",
            "incidents",
            "investigations",
            "kb_chunks",
            "remediation_proposals",
        ]


class TestCreateAllIdempotent:
    def test_second_create_all_is_noop(self, engine):
        """D-30：create_all 幂等——现库 oncall.db 重复执行不报错、列集合不变。"""
        create_tables(engine)  # 二次建表
        names = sorted(inspect(engine).get_table_names())
        assert names == [
            "alert_events",
            "evidence_steps",
            "hypotheses",
            "incidents",
            "investigations",
            "kb_chunks",
            "remediation_proposals",
        ]
        cols = {c["name"] for c in inspect(engine).get_columns("evidence_steps")}
        assert cols == EVIDENCE_STEP_COLUMNS


class TestInsertQueryUpdate:
    def test_investigation_roundtrip_and_finalize(self, engine):
        """会话级字段：running 初始态默认值 → 终态更新（D-28 收尾结构字段）。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_investigation(incident_id))
            session.commit()

            row = session.scalars(select(Investigation)).one()
            assert row.status == "running"
            assert row.step_count == 0
            assert row.total_tokens == 0
            assert row.total_cost_cny == 0.0
            assert row.stop_reason is None
            assert row.finished_at is None

            row.status = "concluded"
            row.stop_reason = "planner_conclusion"
            row.conclusion = "CPU 饥饿导致事件循环阻塞"
            row.failure_mode = None
            row.step_count = 4
            row.total_tokens = 1200
            row.total_cost_cny = 0.004
            row.finished_at = STEP_TS.replace(minute=3)
            session.commit()

            reloaded = session.scalars(select(Investigation)).one()
            assert reloaded.status == "concluded"
            assert reloaded.stop_reason == "planner_conclusion"
            assert reloaded.conclusion == "CPU 饥饿导致事件循环阻塞"
            assert reloaded.step_count == 4
            assert reloaded.total_tokens == 1200
            assert reloaded.total_cost_cny == 0.004
            assert _aware(reloaded.finished_at) == STEP_TS.replace(minute=3)

    def test_evidence_step_roundtrip(self, engine):
        """证据步全字段可插入/查询：output 双存、input 全文（D-32）。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_step(incident_id))
            session.commit()

            row = session.scalars(select(EvidenceStep)).one()
            assert row.step_no == 1
            assert row.tool == "query_metrics"
            assert row.input_json["query"] == "rate(process_cpu_seconds_total[5m])"
            assert row.output_json["values"] == [0.83]
            assert row.output_summary == "api-gw-1 CPU 83%"
            assert row.latency_ms == 120
            assert _aware(row.ts) == STEP_TS

    def test_hypothesis_roundtrip_with_step_references(self, engine):
        """假设三态与步号引用数组（supporting/against）可插入/查询。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(
                _make_hypothesis(
                    incident_id,
                    status="confirmed",
                    supporting_steps=[1, 2],
                    against_steps=[3],
                )
            )
            session.commit()

            row = session.scalars(select(Hypothesis)).one()
            assert row.status == "confirmed"
            assert row.supporting_steps == [1, 2]
            assert row.against_steps == [3]


class TestConstraints:
    def test_foreign_key_rejects_unknown_incident(self, engine):
        """FK 锚 incidents.id：非法 incident_id 拒绝（三表同款，抽 evidence_steps 验）。"""
        with Session(engine) as session:
            session.add(_make_step(incident_id=999))
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                session.commit()

    def test_investigation_incident_id_unique_conflict(self, engine):
        """D-31：incident 1:1——同 incident 二次插入调查记录冲突（覆盖语义锚点）。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_investigation(incident_id))
            session.add(_make_investigation(incident_id))
            with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
                session.commit()

    def test_investigation_status_check_rejects_bogus(self, engine):
        """investigations.status 四态冻结（running/concluded/escalated/aborted）。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_investigation(incident_id, status="bogus"))
            with pytest.raises(IntegrityError):
                session.commit()

    def test_hypothesis_status_check_rejects_bogus(self, engine):
        """hypotheses.status 三态冻结（confirmed/rejected/active）。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_hypothesis(incident_id, status="bogus"))
            with pytest.raises(IntegrityError):
                session.commit()

    def test_duplicate_step_no_within_incident_rejected(self, engine):
        """(incident_id, step_no) 组合唯一：同调查步号重复拒绝。"""
        with Session(engine) as session:
            incident_id = _new_incident(session)
            session.add(_make_step(incident_id, step_no=1))
            session.add(_make_step(incident_id, step_no=1, thought="重复步号"))
            with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
                session.commit()

    def test_same_step_no_across_incidents_allowed(self, engine):
        """组合唯一的另一面：不同 incident 各自从 1 起计步不被误拒。"""
        with Session(engine) as session:
            first = _new_incident(session)
            second = _new_incident(session)
            session.add(_make_step(first, step_no=1))
            session.add(_make_step(second, step_no=1))
            session.commit()

            rows = session.scalars(select(EvidenceStep).order_by(EvidenceStep.id)).all()
            assert [r.incident_id for r in rows] == [first, second]
