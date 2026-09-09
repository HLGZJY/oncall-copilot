"""恢复验证器接缝（M5 issue 04 / T4 / D-44）。

本票只定义注入接缝（结构协议，照 issue 02 `ProposalCreator` 先例）；
真实实现（runbook verification PromQL 回查 + 恢复判据机械判定）归 issue 06
在同名模块落位。api/remediation 只经 deps 拿实例，不感知实现。

D-44 语义：验证判据来自 runbook verification 显式声明，验证器只做机械判定；
返回 dict 落 `proposal.verify_result_json`（D-46 终态字段，验收③「验证结果」
来源）。本票替身返回 `{"recovered": bool, ...}`——`recovered=True` →
`mark_recovered`；`False` → `mark_failed`（回滚/转人工分叉归 06）。
"""

from __future__ import annotations

from typing import Any, Protocol

__all__ = ["RecoveryVerifier"]


class RecoveryVerifier(Protocol):
    """恢复验证器接缝：回查判据，返回含 `recovered` 布尔的验证结果 dict。"""

    def verify(self, dry_run_json: dict[str, Any]) -> dict[str, Any]: ...
