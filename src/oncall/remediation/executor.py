"""受控执行器接缝（M5 issue 04 / T4 / D-48）。

本票只定义注入接缝（结构协议，照 issue 02 `ProposalCreator` 先例）；
真实实现（命令白名单校验 + demo 容器 subprocess 执行 + 执行审计）归 issue 05
在同名模块落位。api/remediation 只经 deps 拿实例，不感知实现。

D-39 语义：执行器收到的唯一输入 = `proposal.dry_run_json`（批准对象）——
api 层与状态机都不改写它，执行清单从批准到执行零漂移（设计风险 4 收口）。
"""

from __future__ import annotations

from typing import Any, Protocol

__all__ = ["RemediationExecutor"]


class RemediationExecutor(Protocol):
    """受控执行器接缝：消费 dry_run_json 命令清单，返回执行输出摘要。

    返回 dict 落 `proposal.params_json["execution"]`（D-46 行内执行留痕载体，
    GET 处置查询的「执行输出摘要」来源）；issue 05 实装后键形状随实现细化。
    """

    def execute(self, dry_run_json: dict[str, Any]) -> dict[str, Any]: ...
