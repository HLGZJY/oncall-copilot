"""M3 六工具包：统一形状（schemas）+ 注册与执行（registry）。

真实数据源取证实现落 issue 03（T3）；本包零 HTTP、零 SDK（C3/C4/C5）。
"""

from oncall.harness.tools.registry import (
    TOOL_NAMES,
    ToolExecution,
    ToolHandler,
    ToolRegistry,
    ToolSpec,
    register_six_tools,
)
from oncall.harness.tools.schemas import (
    DetectAnomalyInput,
    ExecuteActionInput,
    GetTopologyInput,
    QueryKbInput,
    QueryMetricsInput,
    SearchLogsInput,
    ToolResult,
    ToolStatus,
)

__all__ = [
    "TOOL_NAMES",
    "DetectAnomalyInput",
    "ExecuteActionInput",
    "GetTopologyInput",
    "QueryKbInput",
    "QueryMetricsInput",
    "SearchLogsInput",
    "ToolExecution",
    "ToolHandler",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "ToolStatus",
    "register_six_tools",
]
