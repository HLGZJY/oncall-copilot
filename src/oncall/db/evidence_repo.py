"""证据仓库写入接缝（M4-T2；D-33 步进即写 / D-34 会话级成本 / D-25 行 id 回填）。

职责：调查会话的步进/假设入池/终态收尾与 DB 写在同一点——每步一个事务（D-33），
收尾把会话级字段落 `investigations` 行（D-34：落库即取 `InvestigationResult.total_*`
两字段，不做步级摊销）；落库后行 id 经实例内 session↔row 对账表回填（D-25 内存
契约不倒改、指针接口不变；C8：映射封装实例状态不落模块级）。

依赖方向（设计文档「C3/C6 论证」）：harness → db 单向合法；本模块**不 import
oncall.harness**——会话/步/假设/收尾结构全走鸭子类型（内存契约字段见
harness/session.py 与 loop.InvestigationResult），「写失败熔断归类 tool_error」
的归类决策留在 loop 侧；覆盖语义（D-31）：begin 时清理同 incident 旧证据/假设行
与旧映射，保证「库内 = 最近一次调查」。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from oncall.db.models import EvidenceStep as EvidenceStepRow
from oncall.db.models import Hypothesis as HypothesisRow
from oncall.db.models import Investigation

__all__ = ["EvidenceRepository", "persist_hypothesis", "persist_step"]


class EvidenceRepository:
    """步进即写证据仓库（踩坑⑨：实例生命周期 = 单次调查，组装点每请求新建）。

    对账表（step_no → 行 id / 池下标 → 行 id）封装实例状态；同 incident 二次
    调查 begin 时清旧映射，防陈旧行 id 回填错步。
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._investigation_row_id: int | None = None
        self._step_rows: dict[int, int] = {}  # step_no → evidence_steps.id（D-25 回填）
        self._hypothesis_rows: dict[int, int] = {}  # 假设池下标 → hypotheses.id

    def step_row_id(self, step_no: int) -> int | None:
        """行 id 回填查询（D-25：内存指针接口不变，行 id 经此处对账）。"""
        return self._step_rows.get(step_no)

    def hypothesis_row_id(self, index: int) -> int | None:
        """假设池下标 → 行 id（下标在 verifier 按序替换下保持稳定）。"""
        return self._hypothesis_rows.get(index)

    def begin(self, session: Any) -> None:
        """调查开局：建/重置 running 行 + 覆盖清理（D-31：库内 = 最近一次调查）。"""
        incident_id = session.incident_id
        with Session(self._engine) as db:
            db.execute(delete(HypothesisRow).where(HypothesisRow.incident_id == incident_id))
            db.execute(delete(EvidenceStepRow).where(EvidenceStepRow.incident_id == incident_id))
            row = db.scalar(select(Investigation).where(Investigation.incident_id == incident_id))
            if row is None:
                row = Investigation(incident_id=incident_id)
                db.add(row)
            else:  # 覆盖：会话级字段重置回 running 初始态
                row.status = "running"
                row.stop_reason = None
                row.conclusion = None
                row.failure_mode = None
                row.step_count = 0
                row.total_tokens = 0
                row.total_cost_cny = 0.0
                row.started_at = datetime.now(UTC)
                row.finished_at = None
            db.flush()
            self._investigation_row_id = row.id
            db.commit()
        self._step_rows.clear()
        self._hypothesis_rows.clear()

    def record_step(self, session: Any, step: Any) -> int:
        """步进即写（D-33）：一步一事务 INSERT，返回行 id 供指针回填。

        内存契约字段 `cost_cny` ↔ 冻结列名 `cost`（架构 §4，D-25 不倒改内存侧）。
        """
        row = EvidenceStepRow(
            incident_id=session.incident_id,
            step_no=step.step_no,
            thought=step.thought,
            tool=step.tool,
            input_json=dict(step.input_json),
            output_json=dict(step.output_json),
            output_summary=step.output_summary,
            tokens=step.tokens,
            cost=step.cost_cny,
            latency_ms=step.latency_ms,
            ts=step.ts,
        )
        with Session(self._engine) as db:
            db.add(row)
            db.flush()
            row_id = row.id
            db.commit()
        self._step_rows[step.step_no] = row_id
        return row_id

    def add_hypothesis(self, session: Any, hypothesis: Any) -> int:
        """假设入池同步写（D-33）；StrEnum 归一小写串对齐 CHECK 约束（踩坑⑤）。

        先库后内存（persist_hypothesis 顺序），入池前下标 = 池尾。
        """
        index = len(session.hypotheses)
        row = HypothesisRow(
            incident_id=session.incident_id,
            text=hypothesis.text,
            status=str(hypothesis.status),
            supporting_steps=list(hypothesis.supporting_steps),
            against_steps=list(hypothesis.against_steps),
        )
        with Session(self._engine) as db:
            db.add(row)
            db.flush()
            row_id = row.id
            db.commit()
        self._hypothesis_rows[index] = row_id
        return row_id

    def finalize(self, result: Any, *, finished_at: datetime) -> None:
        """终态收尾：`investigations` 行会话级字段 + 假设行状态同步。

        escalated/aborted 路径同样走到（D-28：转人工不是丢弃）；D-34：total 两
        字段落库即取收尾结构现口径，不改收尾结构；假设裁决流转（rejected/
        confirmed）按池下标同步写回（verifier 按序替换 frozen 对象，下标稳定）。
        """
        with Session(self._engine) as db:
            row = db.get(Investigation, self._investigation_row_id)
            row.status = str(result.status)
            row.stop_reason = result.stop_reason
            row.conclusion = result.conclusion
            row.failure_mode = result.failure_mode
            row.step_count = result.step_count
            row.total_tokens = result.total_tokens
            row.total_cost_cny = result.total_cost_cny
            row.finished_at = finished_at
            for index, hypothesis in enumerate(result.hypotheses):
                hyp_id = self._hypothesis_rows.get(index)
                if hyp_id is None:
                    continue
                hyp_row = db.get(HypothesisRow, hyp_id)
                if hyp_row is not None:
                    hyp_row.status = str(hypothesis.status)
            db.commit()


def persist_step(repo: Any, session: Any, step: Any) -> bool:
    """步进即写接缝（D-33）：先 DB 后内存；repo 为 None 仅写内存（既有单测零回退）。

    落库异常不静默吞：abort 会话并返回 False，由 loop 归类 `tool_error`；
    写失败时该步不进 session（避免内存/库双头不一致，踩坑⑩）。
    """
    if repo is None:
        session.record_step(step)
        return True
    try:
        repo.record_step(session, step)
    except Exception as exc:  # 落库失败必须可见：熔断转人工（D-33）
        session.abort(f"证据落库失败：{exc}")
        return False
    session.record_step(step)
    return True


def persist_hypothesis(repo: Any, session: Any, hypothesis: Any) -> bool:
    """假设入池接缝（D-33）：先 DB 后内存；失败语义同 persist_step。"""
    if repo is None:
        session.add_hypothesis(hypothesis)
        return True
    try:
        repo.add_hypothesis(session, hypothesis)
    except Exception as exc:  # 落库失败必须可见：熔断转人工（D-33）
        session.abort(f"假设落库失败：{exc}")
        return False
    session.add_hypothesis(hypothesis)
    return True
