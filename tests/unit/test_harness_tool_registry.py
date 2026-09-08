"""M3-02 ToolRegistry 测试（D-23 统一形状 / 架构 §3.4 三闸 / G4 截断指针）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from oncall.harness.permission import PermissionLevel
from oncall.harness.tools.execute import build_execute_action_handler
from oncall.harness.tools.registry import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RETRIES,
    TOKEN_LIMIT,
    TOOL_NAMES,
    ToolParamError,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    ToolStatus,
    ToolTimeoutError,
    ToolTransportError,
    UnknownToolError,
    query_kb_stub,
    register_six_tools,
)
from oncall.harness.tools.schemas import QueryKbInput, QueryMetricsInput
from oncall.remediation.runbook import load_runbook_library  # 组装点测试可 import（C3 豁免）

FIXED_NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS_DIR = REPO_ROOT / "remediation" / "runbooks"
VALID_ARGS: dict[str, Any] = {
    "promql": "up",
    "start": "2026-09-08T00:00:00Z",
    "end": "2026-09-08T00:05:00Z",
}
FORENSIC_TOOLS = ("query_metrics", "search_logs", "detect_anomaly", "get_topology")


def ok_result(tool: str = "query_metrics", data: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(tool=tool, status=ToolStatus.OK, data=data or {"series": 1}, meta={})


def make_registry(handler: Any = None) -> ToolRegistry:
    registry = ToolRegistry(now=lambda: FIXED_NOW)
    spec = ToolSpec("query_metrics", QueryMetricsInput, PermissionLevel.L0, "查指标")
    registry.register(spec, handler or (lambda args, *, timeout_seconds: ok_result()))
    return registry


class TestRegistration:
    def test_six_tool_names_exact_match(self) -> None:
        assert set(TOOL_NAMES) == {
            "query_metrics",
            "search_logs",
            "detect_anomaly",
            "get_topology",
            "query_kb",
            "execute_action",
        }

    def test_register_and_query(self) -> None:
        registry = make_registry()
        assert registry.names() == {"query_metrics"}
        assert registry.level("query_metrics") is PermissionLevel.L0

    def test_duplicate_registration_rejected(self) -> None:
        registry = make_registry()
        with pytest.raises(ValueError, match="重复注册"):
            registry.register(registry.spec("query_metrics"), query_kb_stub)

    def test_name_outside_fixed_set_rejected(self) -> None:
        registry = make_registry()
        bogus = ToolSpec("kill_pod", QueryKbInput, PermissionLevel.L2, "越权工具")
        with pytest.raises(ValueError, match="固定六工具"):
            registry.register(bogus, query_kb_stub)

    def test_unknown_tool_rejected_with_guidance(self) -> None:
        registry = make_registry()
        with pytest.raises(UnknownToolError, match="可用工具"):
            registry.execute("query_history", {})

    def test_register_six_tools_requires_forensic_handlers(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="执行函数"):
            register_six_tools(registry, {})

    def test_register_six_tools_completes_registry(self) -> None:
        registry = ToolRegistry()
        handlers = {name: query_kb_stub for name in FORENSIC_TOOLS}
        register_six_tools(registry, handlers)
        assert registry.names() == set(TOOL_NAMES)
        assert registry.level("execute_action") is PermissionLevel.L2


class TestParamValidation:
    def test_missing_required_field_rejected_with_guidance(self) -> None:
        registry = make_registry()
        with pytest.raises(ToolParamError, match="promql"):
            registry.execute(
                "query_metrics",
                {"start": VALID_ARGS["start"], "end": VALID_ARGS["end"]},
            )

    def test_extra_field_rejected(self) -> None:
        registry = make_registry()
        bad = dict(VALID_ARGS, bogus=1)
        with pytest.raises(ToolParamError, match="入参校验失败"):
            registry.execute("query_metrics", bad)


class TestExecution:
    def test_ok_execution_fields_aligned_with_evidence_step(self) -> None:
        seen_timeouts: list[float] = []
        seen_args: list[BaseModel] = []

        def handler(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
            seen_timeouts.append(timeout_seconds)
            seen_args.append(args)
            return ok_result()

        registry = make_registry(handler)
        execution = registry.execute("query_metrics", VALID_ARGS, step_no=3)
        assert seen_timeouts == [DEFAULT_TIMEOUT_SECONDS]
        assert isinstance(seen_args[0], QueryMetricsInput)
        assert seen_args[0].promql == "up"
        assert execution.result.status is ToolStatus.OK
        assert execution.ts == FIXED_NOW
        assert execution.cost_cny == 0.0
        assert execution.latency_ms >= 0
        assert execution.tokens > 0
        assert execution.truncated is False

    def test_injectable_timeout_reaches_handler(self) -> None:
        seen: list[float] = []

        def handler(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
            seen.append(timeout_seconds)
            return ok_result()

        registry = ToolRegistry(timeout_seconds=0.5, now=lambda: FIXED_NOW)
        spec = ToolSpec("query_metrics", QueryMetricsInput, PermissionLevel.L0, "查指标")
        registry.register(spec, handler)
        registry.execute("query_metrics", VALID_ARGS)
        assert seen == [0.5]


class TestTimeoutRetry:
    def test_timeout_retries_then_succeeds(self) -> None:
        attempts: list[int] = []

        def flaky(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
            attempts.append(1)
            if len(attempts) <= 2:
                raise ToolTimeoutError("第 1/2 次超时")
            return ok_result()

        execution = make_registry(flaky).execute("query_metrics", VALID_ARGS)
        assert len(attempts) == 3
        assert execution.result.status is ToolStatus.OK

    def test_timeout_retries_exhausted_classified_tool_error(self) -> None:
        attempts: list[int] = []

        def always_timeout(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
            attempts.append(1)
            raise ToolTimeoutError("始终超时")

        execution = make_registry(always_timeout).execute("query_metrics", VALID_ARGS)
        assert len(attempts) == 1 + MAX_RETRIES
        assert execution.result.status is ToolStatus.ERROR
        assert execution.result.meta["failure_mode"] == "tool_error"
        assert execution.result.meta["error_class"] == "ToolTimeoutError"

    def test_transport_error_retries_then_classified_tool_error(self) -> None:
        attempts: list[int] = []

        def always_transport_error(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
            attempts.append(1)
            raise ToolTransportError("连接拒绝")

        execution = make_registry(always_transport_error).execute("query_metrics", VALID_ARGS)
        assert len(attempts) == 1 + MAX_RETRIES
        assert execution.result.meta["failure_mode"] == "tool_error"
        assert "连接拒绝" in str(execution.result.meta["reason"])

    def test_handler_crash_not_retried_and_never_propagates(self) -> None:
        attempts: list[int] = []

        def crashing(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
            attempts.append(1)
            raise RuntimeError("handler 内部炸了")

        execution = make_registry(crashing).execute("query_metrics", VALID_ARGS)
        assert len(attempts) == 1
        assert execution.result.status is ToolStatus.ERROR
        assert execution.result.meta["error_class"] == "handler_crash"
        assert "handler 内部炸了" in str(execution.result.meta["reason"])


class TestTruncation:
    def test_large_output_truncated_with_step_pointer(self) -> None:
        big = {"values": ["x" * 20000]}
        registry = make_registry(lambda args, *, timeout_seconds: ok_result(data=big))
        execution = registry.execute("query_metrics", VALID_ARGS, step_no=7)
        assert execution.truncated is True
        assert execution.tokens <= TOKEN_LIMIT
        assert execution.output_summary.endswith("[truncated, full at step 7]")
        assert execution.result.data == big  # 原始输出完整留 session（G4）

    def test_pointer_without_step_no_mentions_session(self) -> None:
        big = {"values": ["x" * 20000]}
        registry = make_registry(lambda args, *, timeout_seconds: ok_result(data=big))
        execution = registry.execute("query_metrics", VALID_ARGS)
        assert execution.output_summary.endswith("[truncated, full output retained in session]")

    def test_just_under_limit_not_truncated(self) -> None:
        data = {"v": "a" * 7900}  # 摘要 < 8000 字符 → < 2000 tokens
        registry = make_registry(lambda args, *, timeout_seconds: ok_result(data=data))
        execution = registry.execute("query_metrics", VALID_ARGS, step_no=1)
        assert execution.truncated is False
        assert execution.tokens <= TOKEN_LIMIT


class TestStubs:
    def make_full_registry(self) -> ToolRegistry:
        registry = ToolRegistry(now=lambda: FIXED_NOW)
        handlers = {name: query_kb_stub for name in FORENSIC_TOOLS}
        register_six_tools(registry, handlers)
        return registry

    def test_query_kb_stub_returns_unavailable(self) -> None:
        execution = self.make_full_registry().execute("query_kb", {"query": "历史类似事故"})
        assert execution.result.status is ToolStatus.UNAVAILABLE
        assert execution.result.meta["reason"] == "M6 未建库"

    def test_execute_action_stub_when_not_assembled(self) -> None:
        """registry 未注入 execute_action → 回退 stub error（处置执行器未装配纵深防御，裁决①）。"""
        execution = self.make_full_registry().execute(
            "execute_action", {"action": "restart", "params": {"pod": "api-0"}}
        )
        assert execution.result.status is ToolStatus.ERROR
        assert "未装配" in str(execution.result.meta["reason"])

    def test_execute_action_dryrun_injected_via_registry(self) -> None:
        """M5 组装注入干跑 handler（registry handlers 注入面）→ ok + 干跑预览 + proposal_id。"""
        library = load_runbook_library(RUNBOOKS_DIR)
        handler = build_execute_action_handler(
            runbook_loader=library.get,
            proposal_creator=lambda payload: f"pending-{payload['action_id']}",
        )
        registry = ToolRegistry(now=lambda: FIXED_NOW)
        handlers = {name: query_kb_stub for name in FORENSIC_TOOLS}
        handlers["execute_action"] = handler
        register_six_tools(registry, handlers)
        execution = registry.execute(
            "execute_action",
            {"action": "cpu-spike/stop-stress-and-restore-cpuset"},
        )
        assert execution.result.status is ToolStatus.OK
        data = execution.result.data
        assert data["proposal_id"] == "pending-stop-stress-and-restore-cpuset"
        assert data["dry_run_preview"]["runbook_slug"] == "cpu-spike"
        assert data["dry_run_preview"]["commands"]  # 命令清单非空
