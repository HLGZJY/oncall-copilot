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

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from oncall.db import Incident
from oncall.db.evidence_repo import EvidenceRepository
from oncall.db.models import EvidenceStep as EvidenceStepRow
from oncall.db.models import Hypothesis as HypothesisRow
from oncall.db.models import Investigation
from oncall.db.views import investigation_report_body
from oncall.harness.loop import InvestigationResult, LoopComponents, run_investigation
from oncall.harness.session import HypothesisStatus, InvestigationSession

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


# ---------------------------------------------------------------------------
# Markdown 最小版导出（M4-T4 / D-36）：str 模板常量 + 数据直出渲染
#
# D-36：不引入 jinja2 / markdown 库，str.format 拼接零新依赖；最小版是 M6
# 报告生成的**输入**而非替代——时间线美化 / 处置记录 / 改进建议均不在本票。
# D-35 分工：`output_json` 原始全文可回溯走 JSON 报告，正文用 `output_summary`
# （体积不可控，不进 Markdown）。转义不做 HTML/Markdown 处理（数据直出、内部消费）。
# ---------------------------------------------------------------------------

REPORT_MD_HEADER_TEMPLATE = """\
# 调查报告 incident_id={incident_id}

- termination: {termination}
- conclusion: {conclusion}
- failure_mode: {failure_mode}
- step_count: {step_count}
- total_tokens: {total_tokens}
- total_cost_cny: {total_cost_cny}
- stop_reason: {stop_reason}
- confidence: {confidence}
"""

REPORT_MD_STEP_TEMPLATE = """\
## Step {step_no} — {tool}

- thought: {thought}
- input_json: {input_json}
- output_summary: {output_summary}
- ts: {ts}
- tokens: {tokens}
"""

REPORT_MD_HYPOTHESES_HEADER = "## 假设\n"

REPORT_MD_HYPOTHESIS_TEMPLATE = "- [{status}] {text}（支持步: {supporting}｜反对步: {against}）"


def _render_report_markdown(body: dict[str, Any]) -> str:
    """读库报告 dict（`investigation_report_body` 口径）→ Markdown 最小版。

    数据直出零加工：步渲染 `## Step N — {tool}` + thought / input_json /
    output_summary / ts / tokens（票面钉死格式）；`output_json` 不进正文
    （D-36/G7 延伸）；假设渲染 status 与 supporting/against 步号。
    """
    parts = [
        REPORT_MD_HEADER_TEMPLATE.format(
            incident_id=body["incident_id"],
            termination=body["termination"],
            conclusion=body["conclusion"],
            failure_mode=body["failure_mode"],
            step_count=body["step_count"],
            total_tokens=body["total_tokens"],
            total_cost_cny=body["total_cost_cny"],
            stop_reason=body["stop_reason"],
            confidence=body["confidence"],
        )
    ]
    for step in body["steps"]:
        parts.append(
            REPORT_MD_STEP_TEMPLATE.format(
                step_no=step["step_no"],
                tool=step["tool"],
                thought=step["thought"],
                input_json=json.dumps(step["input_json"], ensure_ascii=False, sort_keys=True),
                output_summary=step["output_summary"],
                ts=step["ts"],
                tokens=step["tokens"],
            )
        )
    parts.append(REPORT_MD_HYPOTHESES_HEADER)
    for hyp in body["hypotheses"]:
        parts.append(
            REPORT_MD_HYPOTHESIS_TEMPLATE.format(
                status=hyp["status"],
                text=hyp["text"],
                supporting=", ".join(str(n) for n in hyp["supporting_steps"]) or "无",
                against=", ".join(str(n) for n in hyp["against_steps"]) or "无",
            )
        )
    return "\n".join(parts) + "\n"


@dataclass(frozen=True)
class InvestigationDeps:
    """调查入口注入面（踩坑②：多参收拢 dataclass，照 LoopComponents 先例）。

    `components` 为 None 时 /investigate 落 503——LLM 依赖缺省不静默降级到
    mock（照 classify_runtime 先例，R6：ingest 主链路可用性与本通道无关）；
    `opening_builder` 缺省 None 时由组装点（create_app）按 context 配置兜底。
    M4-T3：D-25 的进程内报告注册表（InvestigationReportStore）整体退役，
    GET 读三表（本 dataclass 不再持有报告状态）。
    """

    components: LoopComponents | None
    opening_builder: OpeningBuilder | None = None


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
            # 票面语义：status ≠ investigating 仍允许调查（不回写 incidents 行），
            # 报告如实记录本次调查结果；开局锚点 = alert_ids[0]（D-19 primary anchor）
            alert_id = incident.alert_ids[0] if incident.alert_ids else None
            try:
                opening = deps.opening_builder(session, alert_id) if alert_id is not None else None
                run_session = InvestigationSession(incident_id=req.incident_id)  # 每次新建会话
                repo = EvidenceRepository(engine)  # 步进即写接缝（D-33；实例=单次调查）
                repo.begin(run_session)  # 覆盖清理 + running 行（D-31）
                result = run_investigation(run_session, replace(deps.components, evidence=repo))
                repo.finalize(result, finished_at=deps.components.now())  # 终态写行（D-34）
            except Exception as exc:  # harness 非预期异常：API 层兜底不泄漏堆栈
                raise HTTPException(
                    status_code=500, detail="调查执行发生非预期异常，已中止"
                ) from exc
        report = build_report(result, opening)
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
        """证据链数据直出的 Markdown 最小版（M4-T4 / D-36 / G7 定案）。

        `text/markdown`，str 模板拼接零新依赖；数据源同 JSON GET（共用
        `_load_report_body`，不重复实现查询逻辑）；本票 Markdown 是 M6
        报告生成的输入而非替代（美化 / 处置 / 建议归 M6）。
        """
        return Response(
            content=_render_report_markdown(_load_report_body(incident_id)),
            media_type="text/markdown",
        )

    return router
