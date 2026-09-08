"""M3/M5 六工具包：统一形状（schemas）+ 注册与执行（registry）+ 干跑 handler（execute）。

真实数据源取证实现落 issue 03（T3）；M5 execute_action 干跑 handler 落本包 execute.py
（issue 02，接 runbook 库 + proposal 存根接缝，C3：不经 import 拿 remediation 数据）。
"""

from oncall.harness.tools.execute import build_execute_action_handler
from oncall.harness.tools.registry import (
    TOOL_NAMES,
    ToolExecution,
    ToolHandler,
    ToolRegistry,
    ToolSpec,
    execute_action_stub,
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
    "build_execute_action_handler",
    "execute_action_stub",
    "register_six_tools",
]
