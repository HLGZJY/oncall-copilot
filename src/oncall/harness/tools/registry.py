"""ToolRegistry：工具注册 / 参数校验 / 超时重试 / 输出截断 / 执行埋点（架构 §3.2/§3.4）。

- 注册面：六工具名集合**精确匹配**（`TOOL_NAMES`），每工具注册 名称 / 入参 schema /
  权限层级 / 执行函数（Protocol 注入，照 `infra/http.py` 的 Fetcher 先例——
  真实数据源 issue 03 才接，本票零 HTTP、零 SDK）
- 执行防护：单工具 30s 超时（超时值随调用传给 handler、由 handler 侧兑现——
  同步执行不另起线程，测试注入假 handler 零真实等待）；失败重试 ≤2（仅超时与
  传输错误重试），重试耗尽归 `tool_error`（D-28）
- 输出截断：>2000 tokens 只动「进上下文的摘要」，`result.data`（原始输出）完整返回，
  由主循环落 session 的 `output_json`（G4 指针语义）；token 估算 = 字符数 ÷ 4
  （保守口径，不引新依赖）；摘要带 `[truncated, full at step N]` 指针
- 执行埋点：tokens / cost_cny / latency_ms / ts 对齐 `EvidenceStep` 字段；
  工具执行无 LLM 消耗，cost_cny 恒 0.0
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from oncall.harness.permission import PermissionLevel
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
    "CHARS_PER_TOKEN",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_RETRIES",
    "TOKEN_LIMIT",
    "TOOL_NAMES",
    "ToolExecution",
    "ToolHandler",
    "ToolParamError",
    "ToolRegistry",
    "ToolSpec",
    "ToolTimeoutError",
    "ToolTransportError",
    "UnknownToolError",
    "execute_action_stub",
    "query_kb_stub",
    "register_six_tools",
]

TOOL_NAMES: tuple[str, ...] = (
    "query_metrics",
    "search_logs",
    "detect_anomaly",
    "get_topology",
    "query_kb",
    "execute_action",
)
DEFAULT_TIMEOUT_SECONDS = 30.0  # 架构 §3.4：单工具 30s 上限
MAX_RETRIES = 2  # 架构 §3.4：失败重试 ≤2（初始 1 次 + 重试 2 次 = 最多 3 次尝试）
TOKEN_LIMIT = 2000  # G2/G4：进上下文的摘要 ≤2000 tokens
CHARS_PER_TOKEN = 4  # token 保守估算口径：字符数 ÷ 4（无分词器依赖）


class UnknownToolError(Exception):
    """未注册的工具名：错误信息列出可用工具，指导 Planner 换向（R7）。"""


class ToolParamError(Exception):
    """入参未过注册 schema 校验：错误信息指明字段与修正方向（R7）。"""


class ToolTimeoutError(Exception):
    """工具执行超时（可重试；真实 handler 侧经 Fetcher timeout 兑现，issue 03）。"""


class ToolTransportError(Exception):
    """工具执行传输失败（可重试；语义对齐 infra.http.HTTPClientError 的传输层口径）。"""


@runtime_checkable
class ToolHandler(Protocol):
    """工具执行函数接缝：入参已过 schema 校验，超时值由 Registry 传入、handler 侧兑现。"""

    def __call__(self, args: BaseModel, *, timeout_seconds: float) -> ToolResult: ...


@dataclass(frozen=True)
class ToolSpec:
    """工具注册元数据：名称 / 入参 schema / 权限层级 / 一句话描述。"""

    name: str
    schema: type[BaseModel]
    level: PermissionLevel
    description: str


@dataclass(frozen=True)
class ToolExecution:
    """一次工具执行的产物：结果 + 摘要 + 埋点（对齐 EvidenceStep 字段，主循环直接落步）。"""

    result: ToolResult
    output_summary: str
    tokens: int
    cost_cny: float
    latency_ms: int
    ts: datetime
    truncated: bool


def query_kb_stub(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
    """query_kb stub（D-08/D-23）：调用即 unavailable，真实 RAG 留 M6。"""
    return ToolResult(
        tool="query_kb",
        status=ToolStatus.UNAVAILABLE,
        data=None,
        meta={"reason": "M6 未建库"},
    )


def execute_action_stub(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
    """execute_action stub（D-23）：L2 由 PermissionGate 拦在执行前；此为纵深防御兜底。"""
    return ToolResult(
        tool="execute_action",
        status=ToolStatus.ERROR,
        data=None,
        meta={"reason": "L2 写操作须经 PermissionGate 与四道闸门（M5 实装）"},
    )


_TOOL_TABLE: tuple[tuple[str, type[BaseModel], PermissionLevel, str], ...] = (
    ("query_metrics", QueryMetricsInput, PermissionLevel.L0, "查指标（Prom query_range）"),
    ("search_logs", SearchLogsInput, PermissionLevel.L0, "查日志（Loki query_range）"),
    ("detect_anomaly", DetectAnomalyInput, PermissionLevel.L0, "时序统计异常检测（v1）"),
    ("get_topology", GetTopologyInput, PermissionLevel.L0, "服务拓扑（复用 context 三源）"),
    ("query_kb", QueryKbInput, PermissionLevel.L0, "历史事故检索（RAG，M6 接入）"),
    ("execute_action", ExecuteActionInput, PermissionLevel.L2, "处置执行（M5 四道闸门）"),
)

TOOL_SPECS: tuple[ToolSpec, ...] = tuple(ToolSpec(*row) for row in _TOOL_TABLE)

_STUB_HANDLERS: dict[str, ToolHandler] = {
    "query_kb": query_kb_stub,
    "execute_action": execute_action_stub,
}


def register_six_tools(registry: ToolRegistry, handlers: Mapping[str, ToolHandler]) -> None:
    """六工具注册面：取证四工具必须注入执行函数；两个 stub 缺省注册（D-23）。"""
    for spec in TOOL_SPECS:
        if spec.name in handlers:
            registry.register(spec, handlers[spec.name])
        elif spec.name in _STUB_HANDLERS:
            registry.register(spec, _STUB_HANDLERS[spec.name])
        else:
            msg = f"工具 {spec.name} 需注入执行函数（真实数据源 issue 03 接）"
            raise ValueError(msg)


class ToolRegistry:
    """工具注册与执行：只做注册/校验/执行/超时/重试/截断/埋点，不改写语义（架构 §3.2）。"""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._now = now or (lambda: datetime.now(UTC))
        self._tools: dict[str, tuple[ToolSpec, ToolHandler]] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        """注册一个工具：名必须在固定六工具集合内，且不可重复注册。"""
        if spec.name not in TOOL_NAMES:
            msg = f"工具名 {spec.name} 不在固定六工具集合内（D-23）：{', '.join(TOOL_NAMES)}"
            raise ValueError(msg)
        if spec.name in self._tools:
            msg = f"工具 {spec.name} 已注册，禁止重复注册"
            raise ValueError(msg)
        self._tools[spec.name] = (spec, handler)

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def spec(self, name: str) -> ToolSpec:
        return self._require(name)[0]

    def level(self, name: str) -> PermissionLevel:
        return self._require(name)[0].level

    def execute(
        self, name: str, raw_args: Mapping[str, Any], *, step_no: int | None = None
    ) -> ToolExecution:
        """校验入参 → 执行（含超时重试）→ 截断摘要 → 产出埋点，永不向上抛工具内部异常。"""
        spec, handler = self._require(name)
        try:
            args = spec.schema.model_validate(dict(raw_args))
        except ValidationError as exc:
            fields = ", ".join(str(err["loc"][0]) if err["loc"] else "?" for err in exc.errors())
            msg = f"工具 {name} 入参校验失败（字段：{fields}）；请按 schema 修正后重试"
            raise ToolParamError(msg) from exc
        started = time.monotonic()
        result = self._invoke(name, handler, args)
        latency_ms = int((time.monotonic() - started) * 1000)
        summary, tokens, truncated = self._summarize(result, step_no)
        return ToolExecution(
            result=result,
            output_summary=summary,
            tokens=tokens,
            cost_cny=0.0,
            latency_ms=latency_ms,
            ts=self._now(),
            truncated=truncated,
        )

    def _require(self, name: str) -> tuple[ToolSpec, ToolHandler]:
        entry = self._tools.get(name)
        if entry is None:
            available = ", ".join(sorted(self._tools)) or "（空）"
            msg = f"未知工具 {name}；可用工具：{available}。请从可用集合中重新选择"
            raise UnknownToolError(msg)
        return entry

    def _invoke(self, name: str, handler: ToolHandler, args: BaseModel) -> ToolResult:
        """超时/传输错误重试 ≤2；其他异常不重试。工具层永不向上抛原始异常（D-16）。"""
        last_error: ToolTimeoutError | ToolTransportError | None = None
        for _ in range(self._max_retries + 1):
            try:
                return handler(args, timeout_seconds=self._timeout_seconds)
            except (ToolTimeoutError, ToolTransportError) as exc:
                last_error = exc
            except Exception as exc:  # pragma: no cover - D-16 兜底，见 meta.reason
                return ToolResult(
                    tool=name,
                    status=ToolStatus.ERROR,
                    data=None,
                    meta={"error_class": "handler_crash", "reason": f"工具执行失败：{exc}"},
                )
        return ToolResult(
            tool=name,
            status=ToolStatus.ERROR,
            data=None,
            meta={
                "error_class": type(last_error).__name__,
                "reason": str(last_error),
                "failure_mode": "tool_error",
                "retries": self._max_retries,
            },
        )

    def _summarize(self, result: ToolResult, step_no: int | None) -> tuple[str, int, bool]:
        """进上下文的摘要 ≤ TOKEN_LIMIT tokens；超限只截摘要，原始输出完整留 result（G4）。"""
        summary = json.dumps(
            {"status": result.status.value, "data": result.data, "meta": result.meta},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        tokens = len(summary) // CHARS_PER_TOKEN
        if tokens <= TOKEN_LIMIT:
            return summary, tokens, False
        pointer = (
            f"[truncated, full at step {step_no}]"
            if step_no is not None
            else "[truncated, full output retained in session]"
        )
        keep = TOKEN_LIMIT * CHARS_PER_TOKEN - len(pointer) - 1
        summary = f"{summary[:keep]}\n{pointer}"
        return summary, len(summary) // CHARS_PER_TOKEN, True
