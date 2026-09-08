"""PermissionGate：权限分级 L0/L1/L2（架构 §3.2/§3.3）。

- L0 只读自动放行 / L1 写需 API 确认（拦截并留待确认态）
- L2 处置权限 = **注入的「处置授权判定器」**（D-41/M5 G3）：`check()` 保持纯函数，L2 放行
  与否由可注入判定器决定。**循环内 L2 默认 = 干跑 ALLOW**（裁决①）：execute_action 在
  loop 内是**干跑请求**——只读渲染命令清单 + 建 pending 处置提案，零 demo 副作用，故放行到
  干跑 handler 出预览；**真实执行批准只存在于确认门/服务层（issue 03/04/05），不经 loop 工具**。
  "有 approved 提案即放行执行" 会允许模型二次执行已批准命令，坏——故真实执行判定器注入在
  服务层，不进本循环门。
- **独立代码路径**：`check()` 只吃工具名、权限层级、入参与注入判定器，与 Planner 输出零耦合
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
    "L2Judge",
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
    # L2 基准判定经注入判定器（_loop_l2_dryrun_allow / 服务层注入的真实授权器），不进本常量决策
}

_L2_REASON = (
    "循环内 L2 execute_action = 干跑请求（只读渲染命令清单 + 建 pending 处置提案，"
    "无 demo 副作用）→ 放行到干跑 handler 出预览；真实执行批准只在确认门/服务层"
    "（M5 issue 03/04/05），不经 loop 工具"
)

L2Judge = Callable[[str, Mapping[str, Any]], tuple[PermissionDecision, str]]


def _loop_l2_dryrun_allow(tool: str, args: Mapping[str, Any]) -> tuple[PermissionDecision, str]:
    """默认 L2 判定器（裁决①）：循环内 execute_action 干跑请求 → ALLOW 放行到干跑 handler。

    干跑无 demo 副作用（只读渲染 + 建 pending 提案）；真实执行从不经 loop 工具（G1/D-39），
    故本循环门对干跑请求一律放行。服务层真实执行授权器在 issue 03 经 `l2_judge` 注入，不进本函数。
    """
    del tool, args
    return PermissionDecision.ALLOWED, _L2_REASON


class PermissionGate:
    """权限闸门：`check()` 依据注册层级判定并落审计，调用方按判定分流执行。

    L2 判定委托给注入的「处置授权判定器」`l2_judge`（默认循环内干跑 ALLOW）；
    L0/L1 走 `LEVEL_DECISIONS` 常量。
    """

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        l2_judge: L2Judge | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._l2_judge: L2Judge = l2_judge or _loop_l2_dryrun_allow
        self.audit_log: list[AuditRecord] = []

    def check(
        self, tool: str, level: PermissionLevel, args: Mapping[str, Any]
    ) -> PermissionDecision:
        """按层级判定：L0 放行 / L1 拦截留待确认 / L2 由注入判定器裁定；每次判定均落审计。"""
        if level is PermissionLevel.L2:
            decision, reason = self._l2_judge(tool, dict(args))
        else:
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
