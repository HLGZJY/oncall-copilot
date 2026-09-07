"""LLM client 接缝（G3 ①）：接口 + Mock 实现，真实 client 留 issue 03/07 接。

设计期唯一实现是 `MockLLMClassifier`（零真实 LLM 调用，2026-09-07 评审拍板）。
它提供三种可编程夹具供 issue 03 兜底测试复用：

- 正常 JSON：返回契约内 `LLMVerdict`（verdict/confidence/reason 可编程）
- 畸形输出：抛 `LLMOutputError`（真实客户端等价于 JSON mode + Pydantic 校验失败）
- 超时：抛 `LLMTimeoutError`（G3 定案 30s 超时，mock 不真等）

`script` 按调用序消费（异常实例或 LLMVerdict 混排），耗尽后稳定回落默认结论——
「畸形 → 重试 → 成功」类编排测试以此为基础设施。
HTTP 与 LLM SDK 只允许在 infra 收口（pyproject C4+C5），本模块零网络依赖，
天然满足 tests/conftest.py 的 autouse 断网 fixture。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from oncall.classify.models import LLMVerdict

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "LLMClassifier",
    "LLMClassifierError",
    "LLMOutputError",
    "LLMTimeoutError",
    "MockLLMClassifier",
]


class LLMClassifierError(Exception):
    """LLM 分类失败基类（issue 03 按具体子类决定重试或落 risk）。"""


class LLMOutputError(LLMClassifierError):
    """畸形输出：契约校验失败（非 JSON / 缺字段 / 枚举越界）。"""


class LLMTimeoutError(LLMClassifierError):
    """调用超时（G3：30s 上限，重试 ≤2）。"""


@runtime_checkable
class LLMClassifier(Protocol):
    """LLM 通道接缝：输入事件卡片（D-17 JSON 契约），输出已校验的 LLMVerdict。

    异常即契约的一部分：实现方把畸形输出与超时表达为上列异常，
    兜底策略（重试 → 失败落 risk，channel=llm_error）由调用方（issue 03）编排。
    """

    def classify(self, alert_card: Mapping[str, Any]) -> LLMVerdict: ...


class MockLLMClassifier:
    """可编程 mock：固定结论 / 畸形输出 / 超时三种夹具，按剧本顺序回放。"""

    def __init__(
        self,
        *,
        verdict: str = "incident",
        confidence: float = 0.8,
        reason: str = "mock 固定结论",
        script: Sequence[LLMVerdict | LLMClassifierError] | None = None,
    ) -> None:
        self._default = LLMVerdict.model_validate(
            {"verdict": verdict, "confidence": confidence, "reason": reason}
        )
        self._script = list(script or [])
        self._cursor = 0
        self.calls: list[Mapping[str, Any]] = []

    def classify(self, alert_card: Mapping[str, Any]) -> LLMVerdict:
        """回放下一条剧本；耗尽后稳定返回默认结论（不抛 StopIteration）。"""
        self.calls.append(alert_card)
        if self._cursor < len(self._script):
            item = self._script[self._cursor]
            self._cursor += 1
            if isinstance(item, LLMClassifierError):
                raise item
            return item
        return self._default
