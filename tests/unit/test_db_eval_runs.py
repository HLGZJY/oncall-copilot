"""M7-T1 验收 · 第十表 eval_runs + 冻结面不破（m7 issue 01 / D-58–D-65）。

照 M6 test_db_kb_chunks 先例：import + 键集合精确断言（不用存在性软断言）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.db.eval_models import EvalRun
from oncall.db.models import Base, KbChunk

# ── 冻结面（只消费不推翻）──

NINE_TABLES = {
    "alert_events",
    "incidents",
    "investigations",
    "evidence_steps",
    "hypotheses",
    "remediation_proposals",
    "kb_chunks",
}
TEN_TABLES = NINE_TABLES | {"eval_runs"}

# D-62 第十表冻结列（字段契约 = m7 设计文档 §数据模型变更）
EVAL_RUN_COLUMNS = {
    "id",
    "scenario",
    "the_set",
    "model",
    "run_idx",
    "verdict",
    "failure_mode",
    "judged_by",
    "step_count",
    "duration_s",
    "tokens",
    "cost_cny",
    "reused",
    "escalated",
    "unstable",
    "run_json",
    "created_at",
}

FAILURE_MODES = {
    "tool_error",
    "plan_error",
    "timeout",
    "hallucination",
    "no_signal",
    "premature_stop",
    "unknown",
}


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


def test_tenth_table_registered_and_frozen_columns():
    """第十表注册进同一 Base 元数据；列集合精确钉死；既有九表不倒改。"""
    assert set(Base.metadata.tables) >= TEN_TABLES
    assert set(EvalRun.__table__.columns.keys()) == EVAL_RUN_COLUMNS


def test_eval_run_create_all_and_roundtrip(db: Session) -> None:
    """create_all 幂等建表 + 一行评测运行 roundtrip（D-62 字段全落可读）。"""
    run = EvalRun(
        scenario="cpu-spike",
        the_set="dev",
        model="qwen3.7-flash",
        run_idx=0,
        verdict="top1",
        failure_mode=None,
        judged_by="rule",
        step_count=6,
        duration_s=42.5,
        tokens=3100,
        cost_cny=0.021,
    )
    db.add(run)
    db.flush()
    row = db.scalar(select(EvalRun).where(EvalRun.id == run.id))
    assert row is not None
    assert row.verdict == "top1"
    assert row.judged_by == "rule"
    assert row.reused is False
    assert row.escalated is False
    assert row.unstable is False
    assert row.run_json == {}
    assert row.failure_mode is None
    assert row.created_at is not None


def test_eval_run_the_set_check_constraint(db: Session) -> None:
    """the_set 二值冻结 dev|holdout：非法值 DB 层拒绝。"""
    db.add(EvalRun(scenario="cpu-spike", the_set="train", model="m", run_idx=0, judged_by="rule"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_eval_run_verdict_check_constraint(db: Session) -> None:
    """verdict 三值冻结 top1|top3|miss：非法值 DB 层拒绝。"""
    db.add(
        EvalRun(
            scenario="cpu-spike",
            the_set="dev",
            model="m",
            run_idx=0,
            verdict="hit",
            judged_by="rule",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_eval_run_judged_by_check_constraint(db: Session) -> None:
    """judged_by 三值冻结 rule|judge|human（D-59）：非法值 DB 层拒绝。"""
    db.add(
        EvalRun(
            scenario="cpu-spike",
            the_set="dev",
            model="m",
            run_idx=0,
            verdict="top1",
            judged_by="auto",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_eval_run_failure_mode_check_constraint(db: Session) -> None:
    """failure_mode 七值冻结（六值 + unknown，D-63）：非法值 DB 层拒绝；null 合法（命中行）。"""
    db.add(
        EvalRun(
            scenario="cpu-spike",
            the_set="dev",
            model="m",
            run_idx=0,
            verdict="miss",
            failure_mode="oops",
            judged_by="rule",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    ok = EvalRun(
        scenario="cpu-spike",
        the_set="dev",
        model="m",
        run_idx=1,
        verdict="miss",
        failure_mode="unknown",
        judged_by="human",
        step_count=9,
        duration_s=60.0,
        tokens=2800,
        cost_cny=0.018,
    )
    db.add(ok)
    db.flush()  # unknown 合法（D-63 人工通道入口）
    assert set(FAILURE_MODES) >= {"unknown"}


def test_eval_run_not_linked_to_investigations(db: Session) -> None:
    """评测行与会话记录分表不混（CONTEXT.md 口径）：eval_runs 无 incident 外键。"""
    assert "incident_id" not in EvalRun.__table__.columns.keys()
    assert not any(fk.referred_table == "investigations" for fk in EvalRun.__table__.foreign_keys)


def test_ninth_table_untouched():
    """第九表 kb_chunks 冻结列回归（第十表新增不得倒改既有表）。"""
    assert set(KbChunk.__table__.columns.keys()) == {
        "id",
        "incident_id",
        "investigation_id",
        "section",
        "seq",
        "text",
        "source_meta_json",
        "hit_count",
        "created_at",
        "superseded_at",
    }
