"""PermissionGate：权限分级 L0/L1/L2（架构 §3.2/§3.3）。

- L0 只读自动放行 / L1 写需 API 确认（拦截并留待确认态）/ L2 永远禁止
- **独立代码路径**：`check()` 只吃工具名、权限层级与入参，与 Planner 输出零耦合
  ——推理与权限强制分离，不可被 prompt 绕过（三道防线：白名单 > 人工确认门 > 提示词）
- 拒绝与拦截均留审计记录（含工具名与入参），审计列表只增不改
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AuditRecord",
    "PermissionDecision",
    "PermissionGate",
    "PermissionLevel",
]


class PermissionLevel(StrEnum):
    """权限三级（CONTEXT.md：三道防线；execute_action 标 L2，D-23）。"""

    L0 = "l0"
    L1 = "l1"
    L2 = "l2"


class PermissionDecision(StrEnum):
    """权限判定三态。"""

    ALLOWED = "allowed"
    NEEDS_CONFIRMATION = "needs_confirmation"
    DENIED = "denied"


class AuditRecord(BaseModel):
    """权限审计记录：放行 / 拦截 / 拒绝逐条落账，含工具名与入参。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(min_length=1)
    level: PermissionLevel
    args: dict[str, Any]
    decision: PermissionDecision
    reason: str
    ts: datetime


LEVEL_DECISIONS: Final[dict[PermissionLevel, tuple[PermissionDecision, str]]] = {
    PermissionLevel.L0: (PermissionDecision.ALLOWED, "L0 只读工具自动放行"),
    PermissionLevel.L1: (
        PermissionDecision.NEEDS_CONFIRMATION,
        "L1 写操作需人工确认（API 层拦截，留待确认态；M5 实装确认门）",
    ),
    PermissionLevel.L2: (
        PermissionDecision.DENIED,
        "L2 永远禁止（M5 实装四道闸门前不开放）",
    ),
}


class PermissionGate:
    """权限闸门：`check()` 依据注册层级判定并落审计，调用方按判定分流执行。"""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self.audit_log: list[AuditRecord] = []

    def check(
        self, tool: str, level: PermissionLevel, args: Mapping[str, Any]
    ) -> PermissionDecision:
        """按层级判定：L0 放行 / L1 拦截留待确认 / L2 拒绝；每次判定均落审计。"""
        decision, reason = LEVEL_DECISIONS[level]
        self.audit_log.append(
            AuditRecord(
                tool=tool,
                level=level,
                args=dict(args),
                decision=decision,
                reason=reason,
                ts=self._now(),
            )
        )
        return decision
