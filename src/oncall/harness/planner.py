"""Planner 接缝（D-22：JSON Mode + Pydantic 契约，M2 同款开法）。

- `PlannerClient` Protocol：`decide(context_view) -> PlannerDecision`，异常即契约一部分
- `PlannerDecision`：决策输出——`{thought, next_tool, args}`（选工具）或
  `{conclusion}`（收束）二选一互斥，模型层校验钉死（CONTEXT.md：决策输出）
- 异常族 `PlannerOutputError` / `PlannerTimeoutError`：**harness 自持**
  （C3 禁 import oncall.classify），语义逐字对齐 M2 `oncall.classify.client`
  的 `LLMOutputError` / `LLMTimeoutError`——畸形重试 ≤2 后归类 `plan_error`，
  超时 30s 不重试；两模块 docstring 互引
- `MockPlanner`：可编程 script 回放（照 M2 `MockLLMClassifier` 先例），
  真实 client 留 issue 03/07 接

HTTP 与 LLM SDK 只允许在 infra 收口（pyproject C4+C5），本模块零网络依赖，
天然满足 tests 的 autouse 断网 fixture；本包零真实 LLM 调用（2026-09-07 拍板）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "MockPlanner",
    "PlannerClient",
    "PlannerDecision",
    "PlannerError",
    "PlannerOutputError",
    "PlannerTimeoutError",
]


class PlannerError(Exception):
    """Planner 调用失败基类（调用方按具体子类决定重试或熔断归类）。"""


class PlannerOutputError(PlannerError):
    """畸形输出：契约校验失败（非 JSON / 缺字段 / 两分支违反互斥）。

    语义对齐 M2 `oncall.classify.client.LLMOutputError`（harness 禁 import classify）。
    """


class PlannerTimeoutError(PlannerError):
    """调用超时（30s 上限，不重试）。

    语义对齐 M2 `oncall.classify.client.LLMTimeoutError`（harness 禁 import classify）。
    """


class PlannerDecision(BaseModel):
    """决策输出（D-22）：两分支二选一互斥，模型层校验钉死。

    选工具分支 `next_tool` + `args` 必须齐备；收束分支只带 `conclusion`。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    thought: str = Field(min_length=1)
    next_tool: str | None = Field(default=None, min_length=1)
    args: dict[str, Any] | None = None
    conclusion: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _branches_mutually_exclusive(self) -> PlannerDecision:
        picks_tool = self.next_tool is not None
        concludes = self.conclusion is not None
        if picks_tool and concludes:
            msg = "两分支互斥：next_tool 与 conclusion 不可同时出现"
            raise ValueError(msg)
        if not picks_tool and not concludes:
            msg = "两分支二选一：必须给出 {next_tool, args} 或 {conclusion} 之一"
            raise ValueError(msg)
        if picks_tool and self.args is None:
            msg = "选工具分支必须带 args（可为空 dict）"
            raise ValueError(msg)
        return self


@runtime_checkable
class PlannerClient(Protocol):
    """Planner 接缝：输入上下文视图（记忆摘要 + 事件锚点，D-25 口径），输出已校验决策。

    异常即契约的一部分：实现方把畸形输出与超时表达为上列异常，
    重试 / 熔断 / 失败模式归类（plan_error）由 Harness 编排。
    """

    def decide(self, context_view: Mapping[str, Any]) -> PlannerDecision: ...


class MockPlanner:
    """可编程 mock：决策 / 畸形输出 / 超时夹具混排，按剧本顺序回放，耗尽稳定回落。"""

    def __init__(
        self,
        *,
        conclusion: str = "mock 默认结论",
        script: Sequence[PlannerDecision | PlannerError] | None = None,
    ) -> None:
        self._default = PlannerDecision.model_validate(
            {"thought": "剧本耗尽，稳定收束", "conclusion": conclusion}
        )
        self._script = list(script or [])
        self._cursor = 0
        self.calls: list[Mapping[str, Any]] = []

    def decide(self, context_view: Mapping[str, Any]) -> PlannerDecision:
        """回放下一条剧本；耗尽后稳定返回默认结论（不抛 StopIteration）。"""
        self.calls.append(context_view)
        if self._cursor < len(self._script):
            item = self._script[self._cursor]
            self._cursor += 1
            if isinstance(item, PlannerError):
                raise item
            return item
        return self._default
