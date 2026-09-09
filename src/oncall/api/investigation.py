"""调查入口 API（issue 07 / T7）：`POST /investigate` + `GET /investigations/{incident_id}`。

契约来源：docs/design/m4-evidence-chain-design.md §API 变更表 +
decisions.md **D-28**（escalate 无 UI 落点 = 状态 + 可查报告——本路由是
「转人工」出口的实现载体，escalated 报告同经 GET 出口）/ **D-25**（M3 进程内
报告注册表，M4 落库后整体退役，GET 换读三表）/ **D-35**（报告 JSON 形状权威
= `build_report` 现契约，读库序列化落 `db/views.py`，键名不倒改）/ **D-31**
（investigations 1:1 覆盖语义——读库天然取到最近一次）/ **D-19**（incidents
status 枚举冻结不扩——status ≠ investigating 仍允许调查，报告如实记录，不
回写 incidents 行）。

- 同步 v1：单次调查 ≤5min 内返回（设计 §风险清单 7；后台任务化不在 M3）
- 零写操作（execute_action 是 L2 stub），四道闸门不适用
- 开局锚点：以 incident 的 `alert_ids[0]` 组装 D-17 事件卡片（时间锚
  `last_fired_at`），随 `investigations.opening_card_json` 留存（本票裁决：
  JSON 列一次性留存而非读时重建——卡内 generated_at 是构建时刻时间戳，重建
  必然漂移，逐字段 roundtrip 不成立）——M8 时间线与取证回放的第一现场
- 报告 JSON 形状权威 = `build_report` 现契约（D-35，键集合由契约测试精确
  守卫；agent-loop-design 示例键名已随本票对齐冻结契约）
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from oncall.api.report_md import render_report_markdown
from oncall.db import Incident
from oncall.db.evidence_repo import EvidenceRepository
from oncall.db.models import EvidenceStep as EvidenceStepRow
from oncall.db.models import Hypothesis as HypothesisRow
from oncall.db.models import Investigation
from oncall.db.views import investigation_report_body
from oncall.harness.loop import InvestigationResult, LoopComponents, run_investigation
from oncall.harness.session import HypothesisStatus, InvestigationSession
from oncall.knowledge.recall import opening_recall
from oncall.knowledge.report import build_closed_loop_report, render_closed_loop_markdown
from oncall.knowledge.retriever import KbRetriever

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

OpeningBuilder = Callable[[Session, int], dict[str, Any] | None]

REPORT_KEYS = frozenset(
    {
        "incident_id",
        "termination",
        "conclusion",
        "failure_mode",
        "step_count",
        "total_tokens",
        "total_cost_cny",
        "stop_reason",
        "confidence",
        "opening_card",
        "steps",
        "hypotheses",
    }
)


logger = logging.getLogger(__name__)


def _reused_report_body(session: Session, reused_from: int) -> dict[str, Any]:
    """缓存复用出口（D-57）：读源事件报告 + `reused_from` 标注（不重查不建新调查）。"""
    src_row = session.scalar(select(Investigation).where(Investigation.incident_id == reused_from))
    if src_row is None or src_row.status == "running":
        raise HTTPException(status_code=404, detail=f"复用源无可用报告: incident_id={reused_from}")
    src_steps = list(
        session.scalars(
            select(EvidenceStepRow)
            .where(EvidenceStepRow.incident_id == reused_from)
            .order_by(EvidenceStepRow.step_no)
        )
    )
    src_hyps = list(
        session.scalars(select(HypothesisRow).where(HypothesisRow.incident_id == reused_from))
    )
    body = investigation_report_body(src_row, src_steps, src_hyps)
    body["reused_from"] = reused_from
    return body


def build_report(
    result: InvestigationResult, opening_card: dict[str, Any] | None
) -> dict[str, Any]:
    """`InvestigationResult` → 证据链报告 JSON（agent-loop-design §数据形状）。

    `termination` 即调查会话终态（concluded/escalated/aborted，D-28）；
    `confidence` 机械口径：已裁决假设（rejected+confirmed 为分母，active 不计）
    中 confirmed 占比，无已裁决假设 → 0.0——M7 LLM-as-judge 复核前先给规则值。
    """
    decided = [h for h in result.hypotheses if h.status is not HypothesisStatus.ACTIVE]
    confirmed = sum(1 for h in decided if h.status is HypothesisStatus.CONFIRMED)
    confidence = round(confirmed / len(decided), 4) if decided else 0.0
    return {
        "incident_id": result.incident_id,
        "termination": result.status.value,
        "conclusion": result.conclusion,
        "failure_mode": result.failure_mode,
        "step_count": result.step_count,
        "total_tokens": result.total_tokens,
        "total_cost_cny": result.total_cost_cny,
        "stop_reason": result.stop_reason,
        "confidence": confidence,
        "opening_card": opening_card,
        "steps": [step.model_dump(mode="json") for step in result.steps],
        "hypotheses": [h.model_dump(mode="json") for h in result.hypotheses],
    }


@dataclass(frozen=True)
class InvestigationDeps:
    """调查入口注入面（踩坑②：多参收拢 dataclass，照 LoopComponents 先例）。

    `components` 为 None 时 /investigate 落 503——LLM 依赖缺省不静默降级到
    mock（照 classify_runtime 先例，R6：ingest 主链路可用性与本通道无关）；
    `opening_builder` 缺省 None 时由组装点（create_app）按 context 配置兜底。
    M4-T3：D-25 的进程内报告注册表（InvestigationReportStore）整体退役，
    GET 读三表（本 dataclass 不再持有报告状态）。
    M6-T4：`kb_retriever` 缺省 None 时开局召回与 query_kb 真实 RAG 不启用
    （query_kb 落 registry stub，既有测试零回退）——注入后开局确定性召回
    （D-54：非 Agent 步）与缓存复用出口（D-57）生效。
    """

    components: LoopComponents | None
    opening_builder: OpeningBuilder | None = None
    kb_retriever: KbRetriever | None = None


class InvestigateRequest(BaseModel):
    """POST /investigate 入参契约（extra=forbid 照 ClassifyRequest 先例）。"""

    model_config = ConfigDict(extra="forbid")

    incident_id: int


def create_investigation_router(engine: Engine, deps: InvestigationDeps) -> APIRouter:
    """调查路由工厂：引擎与 harness 组件由应用工厂注入（测试替身从此进来）。"""
    router = APIRouter()

    @router.post("/investigate")
    def investigate(req: InvestigateRequest) -> dict[str, Any]:
        """同步执行 M3 主循环并返回调查报告（D-28 收尾结构）。"""
        if deps.components is None:
            raise HTTPException(
                status_code=503,
                detail="调查组件未配置：create_app(investigation=...) 注入后可用",
            )
        with Session(engine) as session:
            incident = session.get(Incident, req.incident_id)
            if incident is None:
                raise HTTPException(
                    status_code=404, detail=f"incidents 不存在: id={req.incident_id}"
                )
            # M6-T4 开局召回（D-54/G6：确定性前置，不是 Agent 步、不占步数预算）：
            # 指纹精确命中 + 源 mitigated → 缓存复用出口（D-57，不重查不建新调查）；
            # 未命中向量相似 → kb 参考证据随报告返回（kb_reference 可选键）
            kb_reference: list[dict[str, Any]] | None = None
            if deps.kb_retriever is not None:
                outcome = opening_recall(session, req.incident_id, deps.kb_retriever)
                if outcome.kind == "reuse":
                    return _reused_report_body(session, outcome.reused_from or 0)
                if outcome.kind == "reference":
                    kb_reference = outcome.hits
            # 票面语义：status ≠ investigating 仍允许调查（不回写 incidents 行），
            # 报告如实记录本次调查结果；开局锚点 = alert_ids[0]（D-19 primary anchor）
            alert_id = incident.alert_ids[0] if incident.alert_ids else None
            try:
                opening = deps.opening_builder(session, alert_id) if alert_id is not None else None
                run_session = InvestigationSession(incident_id=req.incident_id)  # 每次新建会话
                repo = EvidenceRepository(engine)  # 步进即写接缝（D-33；实例=单次调查）
                repo.begin(run_session)  # 覆盖清理 + running 行（D-31）
                result = run_investigation(
                    run_session, replace(deps.components, evidence=repo), opening=opening
                )
                repo.finalize(result, finished_at=deps.components.now())  # 终态写行（D-34）
            except Exception as exc:  # harness 非预期异常：API 层兜底不泄漏堆栈，
                # 但堆栈必须落日志（T7 e2e 教训：吞成 500 后故障不可诊断）
                logger.exception("调查执行非预期异常（incident_id=%s）", req.incident_id)
                raise HTTPException(
                    status_code=500, detail="调查执行发生非预期异常，已中止"
                ) from exc
        report = build_report(result, opening)
        if kb_reference:
            # D-54：kb 参考证据（source=kb，语义 = 历史相似案例（参考）非事实）
            report["kb_reference"] = kb_reference
        # opening_card 随行留存（本票裁决：JSON 列一次性留存，读路径零重建）；
        # 写在 finalize 之后（终态行已就位），incident_id 唯一约束保证只此一行
        with Session(engine) as db:
            inv_row = db.scalar(
                select(Investigation).where(Investigation.incident_id == req.incident_id)
            )
            inv_row.opening_card_json = opening
            db.commit()
        return report

    def _load_report_body(incident_id: int) -> dict[str, Any]:
        """三表读库共用路径（M4-T4 抽取）：JSON GET 与 Markdown GET 共用。

        无调查记录或行仍处 running（调查未收尾，终态字段未落）→ 404，
        语义与注册表时代一致；escalated 报告同经出口（D-28）。
        序列化落 `db/views.py`（D-35），api 只组装。
        """
        with Session(engine) as session:
            row = session.scalar(
                select(Investigation).where(Investigation.incident_id == incident_id)
            )
            if row is None or row.status == "running":
                raise HTTPException(
                    status_code=404, detail=f"无调查报告: incident_id={incident_id}"
                )
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

    @router.get("/investigations/{incident_id}")
    def investigation_report(incident_id: int) -> dict[str, Any]:
        """读最近一次调查报告（D-25/D-35：读三表，注册表已退役）。"""
        return _load_report_body(incident_id)

    @router.get("/investigations/{incident_id}/report.md")
    def investigation_report_markdown(incident_id: int) -> Response:
        """闭环报告 Markdown（M6-T2 / D-49：D-36 最小版证据链节保留 + 五节追加）。

        `text/markdown`；前半 = M4 证据链数据直出（键/节零倒改，M7 样本源兼容），
        追加 = knowledge 模块五节闭环报告（开局卡片/时间线/根因/处置/改进建议，
        每数据点可回溯库行，D-49）；五节拼装失败不静默——与 404 语义一致上抛。
        """
        content = render_report_markdown(_load_report_body(incident_id))
        with Session(engine) as session:
            sections = build_closed_loop_report(session, incident_id)
            content += render_closed_loop_markdown(sections)
        return Response(content=content, media_type="text/markdown")

    return router
