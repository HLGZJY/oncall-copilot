"""Alertmanager webhook v4 入参契约（Pydantic v2）。

契约来源：docs/design/m1-alert-ingestion-design.md G3/G5 评审定案 + 评审依据 R1
（prometheus.io webhook_config：version/status/alerts[].fingerprint/truncatedAlerts；
2xx 即确认、非 2xx 指数退避重试）。未知字段一律忽略（前向兼容）；校验失败由
FastAPI 落 4xx——让 Alertmanager 的重试语义可见异常（G3 硬要求），不吞错。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AlertmanagerAlert(BaseModel):
    """v4 payload 中 alerts[] 的单条告警（批量数组元素，不假设单条）。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    status: str
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    # AM 未恢复时发零值哨兵 "0001-01-01T00:00:00Z"，缺省/零值都在归一化层处理
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generator_url: str | None = Field(default=None, alias="generatorURL")
    # AM 自带全 labels FNV-1a 指纹（易变，不作主指纹，仅交叉溯源，见 G5/D-13）
    fingerprint: str


class AlertmanagerWebhook(BaseModel):
    """v4 webhook payload 顶层契约：alerts[] 批量数组 + truncatedAlerts 截断容忍。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    status: str
    alerts: list[AlertmanagerAlert]
    version: str | None = None
    receiver: str | None = None
    group_key: str | None = Field(default=None, alias="groupKey")
    truncated_alerts: int = Field(default=0, alias="truncatedAlerts")
    common_labels: dict[str, str] = Field(default_factory=dict, alias="commonLabels")

    def context(self) -> dict[str, Any]:
        """webhook 级溯源上下文（随每条 alert 存 annotations_json，见 G5）。"""
        return {
            "receiver": self.receiver,
            "status": self.status,
            "group_key": self.group_key,
            "truncated_alerts": self.truncated_alerts,
            "version": self.version,
        }
