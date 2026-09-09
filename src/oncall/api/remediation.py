"""确认门 API（M5 issue 04 / T4 / G2 / D-40 / D-48）：第二道闸门的 API 层落点。

契约来源：docs/design/m5-remediation-gates-design.md §API 契约表（三端点形状
权威）+ decisions.md **D-40**（confirm 即执行——approve 在同一请求内同步遍历
pending→approved→executing→终态返回终态行；提案不自动过期，pending/rejected
永久可查）/ **D-48**（降级形态：未注入受控执行器/恢复验证器 → approve 落
「建议已确认，执行能力未配置」503，proposal 留 approved 即终——approved 是
合法停留态而非中间态遗留；只砍注入面不改契约）/ D-46（第八表字段 = 序列化
形状）/ D-44/D-45/D-28（issue 06 实装：恢复验证判据 = runbook 显式声明；
未恢复 → runbook 显式回滚 → 复验 → recovered/escalated，分叉编排收口在
remediation 层 `run_confirm_chain`，本层只调用）。

安全语义（硬规 3）：确认门是**系统层拦截**（API 层），不是提示词层；批准对象
= `proposal.dry_run_json`（D-39），api 层不改写、执行器只消费该清单。

架构：api → remediation → db 单向合法（与 api→classify→db 同构）；状态判断
只在 remediation 层——api 捕获 `ProposalStateError` 映射 409，不自行查表判断
（单一权威）。零 LLM、零真实外呼（执行器/验证器经注入，未注入即 D-48 降级）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from oncall.db.models import RemediationProposal
from oncall.remediation import service
from oncall.remediation.executor import RemediationExecutor
from oncall.remediation.verifier import RecoveryVerifier, run_confirm_chain

__all__ = ["ConfirmRequest", "RemediationDeps", "create_remediation_router"]

if TYPE_CHECKING:
    from oncall.knowledge.pipeline import KnowledgePipeline


@dataclass(frozen=True)
class RemediationDeps:
    """确认门注入面：受控执行器 + 恢复验证器 + runbook 库（issue 05/06 实装，测试替身即注入）。

    执行器/验证器任一缺省 None → confirm approve 落 D-48 降级（503 + proposal
    留 approved 即终）；runbook 库供回滚分叉取 runbook 显式 rollback（D-45，
    缺失/rollback=[] → 未恢复直边 escalated）；reject 与 GET 查询不受降级影响
    （确认与查询能力独立于执行能力）。
    """

    executor: RemediationExecutor | None = None
    verifier: RecoveryVerifier | None = None
    runbooks: Mapping[str, Any] | None = None
    # M6-T4（D-56）：恢复验证 recovered 后同步触发知识入库；None = 不启用
    # （best-effort：入库失败落日志不阻塞处置出口——D-56 触发语义）
    kb_pipeline: KnowledgePipeline | None = None


class ConfirmRequest(BaseModel):
    """POST /remediations/{id}/confirm 入参契约（extra=forbid 照 ClassifyRequest 先例）。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reason: str | None = None


def _serialize_proposal(row: RemediationProposal) -> dict[str, Any]:
    """proposal 行 → 处置查询 JSON（D-46 字段全量；datetime → ISO 8601）。"""
    return {
        "id": row.id,
        "incident_id": row.incident_id,
        "investigation_id": row.investigation_id,
        "runbook_slug": row.runbook_slug,
        "action_id": row.action_id,
        "status": row.status,
        "dry_run_json": row.dry_run_json,
        "params_json": row.params_json,
        "decision": row.decision,
        "confirm_reason": row.confirm_reason,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "executed_at": row.executed_at.isoformat() if row.executed_at else None,
        "verify_result_json": row.verify_result_json,
        "rollback_status": row.rollback_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def create_remediation_router(engine: Engine, deps: RemediationDeps) -> APIRouter:
    """确认门路由工厂：引擎与执行器/验证器替身由应用工厂注入（测试替身从此进来）。"""
    router = APIRouter()

    @router.post("/remediations/{proposal_id}/confirm")
    def confirm(proposal_id: int, req: ConfirmRequest) -> dict[str, Any]:
        """人工 confirm（D-40）：reject 落终留痕；approve 同步走执行链返回终态行。"""
        with Session(engine) as session:
            row = service.get_proposal(session, proposal_id)
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"remediation_proposals 不存在: id={proposal_id}"
                )
            if req.decision == "reject":
                service.reject(session, row, decision="reject", reason=req.reason)
                return _serialize_proposal(row)
            try:
                # approve 先落库（decision/reason/confirmed_at），503 降级时留 approved 即终
                service.approve(session, row, decision="approve", reason=req.reason)
                if deps.executor is None or deps.verifier is None:
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "建议已确认，执行能力未配置：受控执行器/恢复验证器未注入"
                            "（D-48 降级形态），proposal 留 approved 即终"
                        ),
                    )
                service.start_execution(session, row)
                # 同步链编排收口在 remediation 层（issue 06 裁决）：本层只调用，
                # 一行不重写状态逻辑——执行 → 恢复验证 → 未恢复回滚/转人工分叉
                run_confirm_chain(
                    session,
                    row,
                    executor=deps.executor,
                    verifier=deps.verifier,
                    runbooks=deps.runbooks or {},
                )
            except service.ProposalStateError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if deps.kb_pipeline is not None and row.status == "recovered":
                # D-56：恢复实证（incident mitigated）后同步触发闭环报告入库；
                # best-effort——失败落日志重试语义，不阻塞处置出口（D-56 触发注记）
                try:
                    deps.kb_pipeline.ingest_incident(row.incident_id, session)
                except Exception as exc:
                    import logging

                    logging.getLogger(__name__).warning(
                        "知识入库触发失败（incident_id=%s）：%s", row.incident_id, exc
                    )
            return _serialize_proposal(row)

    @router.get("/remediations/{proposal_id}")
    def get_proposal(proposal_id: int) -> dict[str, Any]:
        """处置查询：干跑预览/状态/确认理由/执行输出摘要/验证结果/回滚状态（D-46 全量）。"""
        with Session(engine) as session:
            row = service.get_proposal(session, proposal_id)
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"remediation_proposals 不存在: id={proposal_id}"
                )
            return _serialize_proposal(row)

    @router.get("/remediations")
    def list_proposals(incident_id: int) -> dict[str, Any]:
        """按事件查处置列表（一对多，多次处置尝试，按 id 序——D-46 锚 incident）。"""
        with Session(engine) as session:
            rows = service.list_by_incident(session, incident_id)
        items = [_serialize_proposal(row) for row in rows]
        return {"incident_id": incident_id, "count": len(rows), "items": items}

    return router
