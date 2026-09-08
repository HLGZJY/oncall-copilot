"""M3-02 PermissionGate 测试（L0/L1/L2 分级 + 审计落账；架构 §3.3 独立代码路径）。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

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

    def test_l2_execute_action_dryrun_allowed_in_loop(self) -> None:
        """裁决①：循环内 L2 execute_action = 干跑请求 → 放行（只读渲染 + 建 pending）。"""
        gate = make_gate()
        decision = gate.check("execute_action", PermissionLevel.L2, {"action": "restart"})
        assert decision is PermissionDecision.ALLOWED

    def test_level_strenum_members_upper_values_lower(self) -> None:
        assert PermissionLevel.L0.value == "l0"
        assert PermissionLevel.L1.value == "l1"
        assert PermissionLevel.L2.value == "l2"


class TestAudit:
    def test_l2_allowance_audited_with_tool_and_args(self) -> None:
        gate = make_gate()
        gate.check(
            "execute_action",
            PermissionLevel.L2,
            {"action": "restart", "params": {"pod": "api-0"}},
        )
        record = gate.audit_log[0]
        assert record.tool == "execute_action"
        assert record.args == {"action": "restart", "params": {"pod": "api-0"}}
        assert record.decision is PermissionDecision.ALLOWED
        assert record.level is PermissionLevel.L2
        assert record.ts == FIXED_NOW
        assert "干跑" in record.reason

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
        """同层级默认判定与工具名/入参无关：默认 L2 干跑 ALLOW（白名单语义，D-05/裁决①）。"""
        gate = make_gate()
        first = gate.check("execute_action", PermissionLevel.L2, {"action": "a"})
        second = gate.check("execute_action", PermissionLevel.L2, {"action": "b"})
        assert first is second is PermissionDecision.ALLOWED

    def test_l2_judge_injectable_without_planner_channel(self) -> None:
        """L2 处置授权判定器可注入（D-41）：有/无 approved 提案 → 不同放行（mock 判定器）。

        check() 保持纯函数：无论注入什么判定器，签名仍是 (tool, level, args)，无推理输入通道；
        判定结果完全由注入判定器决定（服务层 issue 03 用它注入真实 approved-提案授权器）。
        """
        calls: list[tuple[str, dict[str, str]]] = []

        def judge(tool: str, args: Mapping[str, Any]) -> tuple[PermissionDecision, str]:
            calls.append((tool, dict(args)))
            if args.get("approved") is True:
                return PermissionDecision.ALLOWED, "approved 提案存在（mock 判定器）"
            return PermissionDecision.NEEDS_CONFIRMATION, "无 approved 提案 → 待人工确认"

        gate = PermissionGate(now=lambda: FIXED_NOW, l2_judge=judge)
        assert gate.check("execute_action", PermissionLevel.L2, {"approved": True}) is (
            PermissionDecision.ALLOWED
        )
        assert gate.check("execute_action", PermissionLevel.L2, {"approved": False}) is (
            PermissionDecision.NEEDS_CONFIRMATION
        )
        assert calls == [
            ("execute_action", {"approved": True}),
            ("execute_action", {"approved": False}),
        ]
        # 审计记录与判定器结论一致，且 check 仍是纯函数签名
        assert gate.audit_log[0].decision is PermissionDecision.ALLOWED
        assert gate.audit_log[1].decision is PermissionDecision.NEEDS_CONFIRMATION
        assert list(inspect.signature(PermissionGate.check).parameters) == [
            "self",
            "tool",
            "level",
            "args",
        ]
