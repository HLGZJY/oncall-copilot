"""双通道编排落库服务（issue 04 / G4+G5 定案，decisions.md D-19）。

编排顺序（成本四杠杆第 2 位「规则先行」的落点）：
1. 规则通道先行：`run_rule_channel` 命中 → 直判落库，0 次 LLM 调用；
2. 未决行进 LLM 通道：`LLMChannel.classify`——prompt 组装、重试/超时兜底、
   阈值派生全部在通道层完成，服务层只消费 `ClassificationResult`，
   **不吞掉、不改写 channel 语义**（llm_error 兜底行原样进审计字段）；
3. 同一事务落库：`classification_json` 写入 + status 翻 classified 一起提交；
   incident 判定再加 incidents 1:1 建档行（同一事务，不留半状态）。

独立入口（R6）：本服务只由 `POST /classify` 触发，不串联 `/ingest`——
ingest 主链路保持零 LLM 依赖、0 漏收不被外部 LLM 依赖拖垮。

幂等：已 classified 行跳过不重复处理（重复 classify 零副作用、llm_calls=0）；
请求内不存在的 id（并发删除）同样跳过，不中断批次。

LLM 输入卡片：D-17 alert 本体 + 空 context——context 源接入属 issue 07
真实 client 联调范畴，mock 阶段三态判定主要靠 labels/时间模式。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from oncall.classify.models import Verdict
from oncall.classify.rules import MaintenanceWindow, RuleContext, run_rule_channel
from oncall.db import AlertEvent, Incident
from oncall.db.views import alert_body

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from oncall.classify.llm import LLMChannel

__all__ = ["ClassifyOptions", "ClassifyRuntime", "ClassifySummary", "classify_alerts"]

CLASSIFIED_STATUS = "classified"  # 架构 §4 冻结枚举：deduped → classified，不扩
DEFAULT_INCIDENT_SEVERITY = "warning"  # G5：labels.severity 缺省 warning


@dataclass(frozen=True)
class ClassifyOptions:
    """编排环境注入：规则判定时间与生效中的静默窗口（缺省取当前 UTC 时间）。"""

    now: datetime | None = None
    maintenance_windows: tuple[MaintenanceWindow, ...] = ()


@dataclass(frozen=True)
class ClassifyRuntime:
    """POST /classify 的运行时依赖（LLM 通道 + 编排环境），由应用工厂整体注入。

    收敛为单一参数是 ruff max-args 纪律（03 的 `LLMChannelOptions` 先例）；
    `runtime=None` 时 /classify 落 503——LLM 依赖必须显式注入，禁静默 mock。
    """

    llm_channel: LLMChannel
    options: ClassifyOptions | None = None


@dataclass(frozen=True)
class ClassifySummary:
    """POST /classify 响应计数：classified = 本次**新**分类行数（跳过行不计入）。"""

    classified: int
    false_positive: int
    risk: int
    incident: int
    llm_calls: int


def classify_alerts(
    session: Session,
    alert_ids: Sequence[int],
    *,
    llm_channel: LLMChannel,
    options: ClassifyOptions | None = None,
) -> ClassifySummary:
    """按给定 id 序执行双通道编排落库，返回响应计数。

    一次分类一个事务：classification_json + status（+ 建档行）逐行提交，
    批次中单行失败不拖垮已提交的行。
    """
    opts = options or ClassifyOptions()
    now = opts.now or datetime.now(UTC)
    rule_context = RuleContext(now=now, maintenance_windows=opts.maintenance_windows)

    classified = false_positive = risk = incident = llm_calls = 0
    for alert_id in alert_ids:
        row = session.get(AlertEvent, alert_id)
        if row is None or row.status == CLASSIFIED_STATUS:
            continue  # 幂等：已分类行跳过；不存在行不中断批次
        result = run_rule_channel(row, rule_context)
        if result is None:
            result = llm_channel.classify(_alert_card(row, now))
            llm_calls += 1
        row.classification_json = result.model_dump(mode="json")
        row.status = CLASSIFIED_STATUS
        if result.verdict == Verdict.INCIDENT:
            session.add(_file_incident(row, now))
            incident += 1
        elif result.verdict == Verdict.RISK:
            risk += 1
        else:
            false_positive += 1
        classified += 1
        session.commit()
    return ClassifySummary(
        classified=classified,
        false_positive=false_positive,
        risk=risk,
        incident=incident,
        llm_calls=llm_calls,
    )


def _alert_card(row: AlertEvent, generated_at: datetime) -> dict[str, Any]:
    """LLM 通道输入卡片：D-17 alert 本体 + 空 context（context 源接入属 issue 07）。"""
    return {"alert": alert_body(row), "context": {}, "generated_at": generated_at.isoformat()}


def _file_incident(row: AlertEvent, created_at: datetime) -> Incident:
    """真实告警 1:1 建档（G5 最小集五字段）。"""
    return Incident(
        alert_ids=[row.id],  # 单元素数组：归并预留结构，M2 不聚合
        severity=(row.labels_json or {}).get("severity") or DEFAULT_INCIDENT_SEVERITY,
        created_at=created_at,
    )
