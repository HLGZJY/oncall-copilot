"""取证工具共享 helper（issue 03）：时间锚缺省 / 括号配平 / HTTP 超时兑现。

- `default_time_window`：G2① 时间缺省锚 `incident.alert.last_fired_at`（D-17）
  ± D-16 窗口（窗口口径照 `ContextConfig`）——schema 已冻结 start/end 必填，
  缺省填充发生在参数组装侧（issue 06/07 调用本 helper），工具实现与缺省口径同源
- `balanced`：PromQL/LogQL 最小合法性校验（括号/花括号配平），完整语义校验
  交给上游——非 2xx / status!=success 降级为 error（issue 03 票面）
- `effective_timeout`：Registry 传入的 timeout_seconds 与数据源配置超时取小
  （M3-02 语义：超时值由 handler 侧兑现）
"""

from __future__ import annotations

from datetime import datetime, timedelta

Pairs = tuple[datetime, datetime]

_BRACKET_PAIRS = {")": "(", "]": "[", "}": "{"}


def default_time_window(anchor: datetime, window: timedelta) -> Pairs:
    """时间窗缺省口径：[anchor - window, anchor]（anchor = last_fired_at，D-17）。"""
    return anchor - window, anchor


def balanced(expr: str) -> bool:
    """括号/花括号配平最小校验；空串不合法（schema 已挡，防御性再判）。"""
    if not expr:
        return False
    stack: list[str] = []
    for char in expr:
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or stack.pop() != _BRACKET_PAIRS[char]:
                return False
    return not stack


def effective_timeout(timeout_seconds: float, source_timeout: float) -> float:
    """Registry 超时与数据源配置超时取小——handler 侧兑现 30s 上限。"""
    return min(timeout_seconds, source_timeout)
