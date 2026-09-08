"""M3-06 主循环 Loop 测试——防绕圈四机制 / 权限拦截 / EvidenceStep 100% 记录。

D-27 四机制：① 重复调用检测（规范化哈希纯函数 + 连续 ≥2 / 累计 ≥3 警告 +
再犯熔断）② 假设去重拒绝入池 + 换向提示 ③ 步数 ≥10 移出已证伪假设
（视图层，Loop 复用 ContextManager 勿重写）④ Fallback 结构化失败摘要喂回。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 夹具（与 test_harness_loop.py 同法；tests/unit 非包，无法跨文件 import）
# ---------------------------------------------------------------------------
from datetime import UTC, datetime, timedelta
from typing import Any

from oncall.harness.loop import (
    LoopComponents,
    canonical_args,
    normalize_hypothesis_text,
    run_investigation,
)
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner, PlannerDecision
from oncall.harness.session import (
    InvestigationSession,
    SessionStatus,
)
from oncall.harness.tools.registry import (
    ToolRegistry,
    ToolTimeoutError,
    register_six_tools,
)
from oncall.harness.tools.schemas import ToolResult, ToolStatus
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict

_TS = datetime(2026, 9, 8, 8, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self._now = _TS

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


def make_handler(tool: str, result: ToolResult | None = None, *, crash: str | None = None) -> Any:
    def handler(args: Any, *, timeout_seconds: float) -> ToolResult:
        if crash == "timeout":
            raise ToolTimeoutError("查询超时")
        return result or ToolResult(
            tool=tool, status=ToolStatus.OK, data={"direction": "up"}, meta={}
        )

    return handler


DEFAULT_HANDLERS: dict[str, Any] = {
    "query_metrics": make_handler("query_metrics"),
    "search_logs": make_handler("search_logs"),
    "detect_anomaly": make_handler("detect_anomaly"),
    "get_topology": make_handler("get_topology"),
}


def make_components(
    planner: MockPlanner,
    *,
    judge_script: list[Any] | None = None,
    judge_default: VerifierVerdict | None = None,
    clock: FakeClock | None = None,
    handlers: dict[str, Any] | None = None,
) -> LoopComponents:
    now = clock.now if clock is not None else (lambda: _TS)
    registry = ToolRegistry(now=now)
    register_six_tools(registry, handlers if handlers is not None else DEFAULT_HANDLERS)
    return LoopComponents(
        planner=planner,
        registry=registry,
        gate=PermissionGate(now=now),
        verifier=Verifier(judge=MockVerifierJudge(script=judge_script, default=judge_default)),
        now=now,
    )


def tool_decision(
    tool: str = "query_metrics",
    *,
    thought: str = "查消费者延迟",
    args: dict[str, Any] | None = None,
) -> PlannerDecision:
    payload: dict[str, Any] = {
        "thought": thought,
        "next_tool": tool,
        "args": args or {"promql": "queue_lag", "start": _TS.isoformat(), "end": _TS.isoformat()},
    }
    return PlannerDecision.model_validate(payload)


def conclusion_decision(text: str = "队列堆积导致告警") -> PlannerDecision:
    return PlannerDecision.model_validate({"thought": "收束", "conclusion": text})


def make_session() -> InvestigationSession:
    return InvestigationSession(incident_id=1)


# ---------------------------------------------------------------------------
# 机制 ①：重复调用检测
# ---------------------------------------------------------------------------


class TestRepeatDetection:
    def test_canonical_args_is_pure_and_order_insensitive(self) -> None:
        """规范化哈希：args 排序后序列化，纯函数同输入同输出。"""
        a = canonical_args({"b": 1, "a": 2})
        b = canonical_args({"a": 2, "b": 1})
        assert a == b
        assert canonical_args({"a": 1}) != canonical_args({"a": 2})

    def test_normalize_hypothesis_text_pure(self) -> None:
        assert normalize_hypothesis_text("  消费者，延迟！ ") == normalize_hypothesis_text(
            "消费者延迟"
        )

    def test_consecutive_repeat_warns_then_circuit_breaks(self) -> None:
        """连续 ≥2 注入循环警告进下轮 prompt；再犯熔断归 plan_error。"""
        same = {"promql": "queue_lag", "start": _TS.isoformat(), "end": _TS.isoformat()}
        script = [
            tool_decision(args=dict(same)),
            tool_decision(args=dict(same)),
            tool_decision(args=dict(same)),
            conclusion_decision(),
        ]
        planner = MockPlanner(script=script)
        result = run_investigation(make_session(), make_components(planner))
        assert result.status is SessionStatus.ESCALATED
        assert result.failure_mode == "plan_error"
        assert "绕圈" in (result.stop_reason or "")
        warnings = [n for view in planner.calls for n in view["notices"] if "循环" in n]
        assert len(warnings) >= 1  # 警告进过下轮 prompt

    def test_total_repeat_three_non_consecutive_warns(self) -> None:
        """累计 ≥3（不连续）也触发警告；再犯（第 4 次）熔断。"""
        same = {"promql": "queue_lag", "start": _TS.isoformat(), "end": _TS.isoformat()}
        other = {"promql": "cpu", "start": _TS.isoformat(), "end": _TS.isoformat()}
        script = [
            tool_decision(args=dict(same)),
            tool_decision(args=dict(other)),
            tool_decision(args=dict(same)),
            tool_decision(args=dict(same)),
            tool_decision(args=dict(same)),
        ]
        planner = MockPlanner(script=script)
        result = run_investigation(make_session(), make_components(planner))
        assert result.status is SessionStatus.ESCALATED
        assert result.failure_mode == "plan_error"


class TestHypothesisDedupAndFallback:
    def test_duplicate_hypothesis_rejected_from_pool(self) -> None:
        """规范化相似的假设拒绝入池，并注入换向提示。"""
        planner = MockPlanner(
            script=[
                tool_decision(thought="消费者延迟导致队列堆积"),
                tool_decision(thought="消费者延迟导致队列堆积！"),
                conclusion_decision("收束"),
            ]
        )
        result = run_investigation(make_session(), make_components(planner))
        assert len(result.hypotheses) == 1
        notices = [n for view in planner.calls for n in view["notices"]]
        assert any("换向" in n for n in notices)

    def test_fallback_on_tool_failure_feeds_summary(self) -> None:
        """工具重试耗尽 → 结构化失败摘要（含工具名与错误）喂回 Planner。"""
        planner = MockPlanner(script=[tool_decision(), conclusion_decision("换向后收束")])
        handlers = dict(DEFAULT_HANDLERS)
        handlers["query_metrics"] = make_handler("query_metrics", crash="timeout")
        run_investigation(make_session(), make_components(planner, handlers=handlers))
        notices = [n for view in planner.calls for n in view["notices"]]
        fallback = [n for n in notices if "重试耗尽" in n]
        assert fallback and "query_metrics" in fallback[0]

    def test_fallback_on_gate_denial_feeds_summary(self) -> None:
        """L2 拒绝（留审计记录）→ Fallback 摘要喂回，调查不中断。"""
        planner = MockPlanner(
            script=[
                tool_decision(thought="先取证一步"),
                PlannerDecision.model_validate(
                    {
                        "thought": "重启服务",
                        "next_tool": "execute_action",
                        "args": {"action": "restart"},
                    }
                ),
                conclusion_decision("放弃写操作"),
            ]
        )
        session = make_session()
        components = make_components(planner)
        result = run_investigation(session, components)
        assert result.status is SessionStatus.CONCLUDED
        assert result.step_count == 1  # 被拒动作不落证据步（仅取证步落链）
        audit = components.gate.audit_log
        assert len(audit) == 2  # 取证步放行 + 写操作拒绝，均留审计
        assert audit[-1].decision.value == "denied"
        notices = [n for view in planner.calls for n in view["notices"]]
        assert any("权限" in n for n in notices)


# ---------------------------------------------------------------------------
# 机制 ③：步数 ≥10 移出已证伪假设（视图层）
# ---------------------------------------------------------------------------


class TestRejectedHypothesisEviction:
    def test_rejected_hypothesis_hidden_after_step10(self) -> None:
        """步数 ≥10 时 planner 视图中不再出现 rejected 假设（session 全量保留）。"""
        script = [
            tool_decision(thought="初始假设甲"),
            *[
                tool_decision(
                    args={"promql": f"m{i}", "start": _TS.isoformat(), "end": _TS.isoformat()},
                    thought=f"步{i}",
                )
                for i in range(1, 11)
            ],
        ]
        judge_script = [VerifierVerdict(supported=False, reason="证伪甲")]
        planner = MockPlanner(script=script)
        run_investigation(
            make_session(),
            make_components(
                planner,
                judge_script=judge_script,
                judge_default=VerifierVerdict(supported=True, reason="支持"),
            ),
        )
        views_with_hypotheses = [view for view in planner.calls if view["hypotheses"]]
        assert views_with_hypotheses, "早期视图应含假设"
        early = views_with_hypotheses[0]
        late = planner.calls[-1]
        assert any("甲" in h for h in early["hypotheses"])
        assert all("甲" not in h for h in late["hypotheses"])  # ≥10 步移出证伪假设


# ---------------------------------------------------------------------------
# EvidenceStep 100% 记录
# ---------------------------------------------------------------------------


class TestEvidenceStepRecording:
    def test_every_step_records_output_json_and_summary(self) -> None:
        script = [
            tool_decision(),
            tool_decision(
                "search_logs",
                thought="查日志",
                args={"selector": '{job="api"}', "start": _TS.isoformat()},
            ),
            conclusion_decision("收束"),
        ]
        session = make_session()
        clock = FakeClock()
        result = run_investigation(
            session, make_components(MockPlanner(script=script), clock=clock)
        )
        assert result.step_count == 2
        for step in session.steps:
            assert step.output_json, f"步 {step.step_no} 缺 output_json"
            assert step.output_summary, f"步 {step.step_no} 缺 output_summary"
            assert step.output_json["status"] == "ok"
        assert [s.step_no for s in session.steps] == [1, 2]
        assert all(s.ts.tzinfo is not None for s in session.steps)

    def test_timeout_step_still_recorded_with_error_meta(self) -> None:
        """重试耗尽的失败执行同样 100% 落证据步（可回溯）。"""
        handlers = dict(DEFAULT_HANDLERS)
        handlers["query_metrics"] = make_handler("query_metrics", crash="timeout")
        session = make_session()
        result = run_investigation(
            session,
            make_components(
                MockPlanner(script=[tool_decision(), conclusion_decision("收")]), handlers=handlers
            ),
        )
        assert result.step_count == 1
        step = session.steps[0]
        assert step.output_json["status"] == "error"
        assert step.output_json["meta"]["failure_mode"] == "tool_error"

    def test_result_carries_cost_and_token_totals(self) -> None:
        result = run_investigation(
            make_session(),
            make_components(
                MockPlanner(script=[tool_decision(), tool_decision(), conclusion_decision("收")])
            ),
        )
        assert result.total_tokens >= 0
        assert result.total_cost_cny == 0.0  # 工具执行无 LLM 消耗
