"""M3-02 PermissionGate 测试（L0/L1/L2 分级 + 审计落账；架构 §3.3 独立代码路径）。"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

from oncall.harness.permission import (
    PermissionDecision,
    PermissionGate,
    PermissionLevel,
)

FIXED_NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


def make_gate() -> PermissionGate:
    return PermissionGate(now=lambda: FIXED_NOW)


class TestDecisions:
    def test_l0_readonly_allowed(self) -> None:
        gate = make_gate()
        decision = gate.check("query_metrics", PermissionLevel.L0, {"promql": "up"})
        assert decision is PermissionDecision.ALLOWED

    def test_l1_write_needs_confirmation(self) -> None:
        gate = make_gate()
        decision = gate.check("scale_replicas", PermissionLevel.L1, {"to": 3})
        assert decision is PermissionDecision.NEEDS_CONFIRMATION

    def test_l2_always_denied(self) -> None:
        gate = make_gate()
        decision = gate.check("execute_action", PermissionLevel.L2, {"action": "restart"})
        assert decision is PermissionDecision.DENIED

    def test_level_strenum_members_upper_values_lower(self) -> None:
        assert PermissionLevel.L0.value == "l0"
        assert PermissionLevel.L1.value == "l1"
        assert PermissionLevel.L2.value == "l2"


class TestAudit:
    def test_l2_denial_audited_with_tool_and_args(self) -> None:
        gate = make_gate()
        gate.check(
            "execute_action",
            PermissionLevel.L2,
            {"action": "restart", "params": {"pod": "api-0"}},
        )
        record = gate.audit_log[0]
        assert record.tool == "execute_action"
        assert record.args == {"action": "restart", "params": {"pod": "api-0"}}
        assert record.decision is PermissionDecision.DENIED
        assert record.level is PermissionLevel.L2
        assert record.ts == FIXED_NOW

    def test_l1_interception_audited(self) -> None:
        gate = make_gate()
        gate.check("scale_replicas", PermissionLevel.L1, {"to": 3})
        record = gate.audit_log[0]
        assert record.decision is PermissionDecision.NEEDS_CONFIRMATION
        assert "确认" in record.reason

    def test_audit_log_append_only(self) -> None:
        gate = make_gate()
        gate.check("a", PermissionLevel.L0, {})
        gate.check("b", PermissionLevel.L2, {})
        assert len(gate.audit_log) == 2
        assert [r.tool for r in gate.audit_log] == ["a", "b"]


class TestIndependentCodePath:
    def test_check_signature_has_no_planner_or_prompt_channel(self) -> None:
        """架构 §3.3：权限判定与 Planner 输出零耦合——签名上不存在任何推理输入通道。"""
        params = list(inspect.signature(PermissionGate.check).parameters)
        assert params == ["self", "tool", "level", "args"]

    def test_decision_is_pure_function_of_level(self) -> None:
        """同层级无论传什么工具名/入参，判定不变（白名单语义，D-05）。"""
        gate = make_gate()
        first = gate.check("execute_action", PermissionLevel.L2, {"action": "a"})
        second = gate.check("execute_action", PermissionLevel.L2, {"action": "b"})
        assert first is second is PermissionDecision.DENIED
