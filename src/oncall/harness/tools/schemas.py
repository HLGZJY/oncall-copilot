"""M3 六工具统一形状与入参 schema（D-23：形状一次到位，不再改）。

- `ToolResult`：工具结果统一封装 `{tool, status, data, meta}`（CONTEXT.md：工具结果；
  unavailable 语义对齐 D-16——依赖缺失 ≠ 调查失败，工具层永不向上抛原始异常）
- 六工具入参 schema：按 G2 定案冻结 I/O 形状；取证四工具（query_metrics /
  search_logs / detect_anomaly / get_topology）的执行实现落 issue 03（T3），
  本票只冻结入参形状供 ToolRegistry 校验

token 估算口径（与 registry.py 一致）：字符数 ÷ 4 保守估算，不引新依赖。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "DetectAnomalyInput",
    "ExecuteActionInput",
    "GetTopologyInput",
    "QueryKbInput",
    "QueryMetricsInput",
    "SearchLogsInput",
    "ToolResult",
    "ToolStatus",
]


class ToolStatus(StrEnum):
    """工具结果四态（D-23；StrEnum 成员大写、值为小写串，照 M3-01 先例）。"""

    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class ToolResult(BaseModel):
    """工具结果（D-23 统一形状；data/meta 允许缺省——unavailable stub 只带 reason）。"""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1)
    status: ToolStatus
    data: dict[str, object] | None = None
    meta: dict[str, object] = Field(default_factory=dict)


class QueryMetricsInput(BaseModel):
    """query_metrics 入参（G2①：Prometheus query_range）。

    时间窗缺省锚定告警 last_fired_at ± D-16 窗口，收口在 issue 03（T3）。
    """

    model_config = ConfigDict(extra="forbid")

    promql: str = Field(min_length=1)
    start: datetime
    end: datetime
    step: str | None = Field(
        default=None,
        description="步长（如 15s），缺省由实现侧定",
    )


class SearchLogsInput(BaseModel):
    """search_logs 入参（G2②：Loki query_range；limit ≤100、direction 默认 backward，R4）。"""

    model_config = ConfigDict(extra="forbid")

    selector: str = Field(min_length=1, description="LogQL 流选择器")
    start: datetime
    end: datetime | None = None
    limit: int = Field(default=100, ge=1, le=100)
    direction: Literal["backward", "forward"] = "backward"


class DetectAnomalyInput(BaseModel):
    """detect_anomaly 入参（G3：纯统计 v1，喂时序点列）。"""

    model_config = ConfigDict(extra="forbid")

    values: list[float] = Field(min_length=1)
    timestamps: list[datetime] = Field(min_length=1)

    @model_validator(mode="after")
    def _same_length(self) -> DetectAnomalyInput:
        if len(self.values) != len(self.timestamps):
            msg = "values 与 timestamps 必须等长且按位对齐"
            raise ValueError(msg)
        return self


class GetTopologyInput(BaseModel):
    """get_topology 入参（G2⑤：复用 oncall.context 三源；服务维度可选过滤）。"""

    model_config = ConfigDict(extra="forbid")

    service: str | None = Field(default=None, description="可选：聚焦单个服务")


class QueryKbInput(BaseModel):
    """query_kb 入参（G2④：M3 为 unavailable stub，形状先冻结）。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=10)


class ExecuteActionInput(BaseModel):
    """execute_action 入参（G2⑥：L2 stub；M5 四道闸门实装时沿用）。"""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    params: dict[str, object] = Field(default_factory=dict)
