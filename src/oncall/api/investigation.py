"""调查入口 API（issue 07 / T7）：`POST /investigate` + `GET /investigations/{incident_id}`。

契约来源：docs/design/m3-investigation-loop-design.md §API 变更表 +
decisions.md **D-28**（escalate 无 UI 落点 = 状态 + 可查报告——本路由是
「转人工」出口的实现载体，escalated 报告同经 GET 出口）/ **D-25**（报告
注册表进程内存持有，M4 落库后整体替换为读表）/ **D-19**（incidents.status
枚举冻结不扩——status ≠ investigating 仍允许调查，报告如实记录，不回写
incidents 行）。

- 同步 v1：单次调查 ≤5min 内返回（设计 §风险清单 7；后台任务化不在 M3）
- 零写操作（execute_action 是 L2 stub），四道闸门不适用
- 开局锚点：以 incident 的 `alert_ids[0]` 组装 D-17 事件卡片（时间锚
  `last_fired_at`），随报告落 `opening_card`——M8 时间线与取证回放的第一现场
- 报告 JSON 形状照 docs/architecture/agent-loop-design.md §证据链数据形状
  （steps / hypotheses / conclusion / confidence），键集合由契约测试精确守卫
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from oncall.db import Incident
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


class InvestigationReportStore:
    """进程内报告注册表（D-25：dev 单进程语义；M4 落库后整体替换为读表）。

    同 incident 重复调查覆盖旧报告——注册表只保最近一次（容量防御）。
    """

    def __init__(self) -> None:
        self._reports: dict[int, dict[str, Any]] = {}

    def save(self, incident_id: int, report: dict[str, Any]) -> None:
        self._reports[incident_id] = report

    def get(self, incident_id: int) -> dict[str, Any] | None:
        return self._reports.get(incident_id)


@dataclass(frozen=True)
class InvestigationDeps:
    """调查入口注入面（踩坑②：多参收拢 dataclass，照 LoopComponents 先例）。

    `components` 为 None 时 /investigate 落 503——LLM 依赖缺省不静默降级到
    mock（照 classify_runtime 先例，R6：ingest 主链路可用性与本通道无关）；
    `opening_builder` 缺省 None 时由组装点（create_app）按 context 配置兜底。
    """

    components: LoopComponents | None
    store: InvestigationReportStore = field(default_factory=InvestigationReportStore)
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
                result = run_investigation(
                    InvestigationSession(incident_id=req.incident_id),  # 踩坑⑪：每次新建会话
                    deps.components,
                )
            except Exception as exc:  # harness 非预期异常：API 层兜底不泄漏堆栈
                raise HTTPException(
                    status_code=500, detail="调查执行发生非预期异常，已中止"
                ) from exc
        report = build_report(result, opening)
        deps.store.save(req.incident_id, report)
        return report

    @router.get("/investigations/{incident_id}")
    def investigation_report(incident_id: int) -> dict[str, Any]:
        """读最近一次调查报告；escalated 报告同经此出口（D-28 转人工落点）。"""
        report = deps.store.get(incident_id)
        if report is None:
            raise HTTPException(status_code=404, detail=f"无调查报告: incident_id={incident_id}")
        return report

    return router
