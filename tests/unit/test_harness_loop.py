"""M3-06 主循环 Loop 测试（D-27/D-28 定案口径）——骨架 / 终止三出口 / 六值归类。

- G8 防伪三判据：判据 1 两脚本对照 / 判据 2 不调 get_topology 亦收束 /
  判据 3 推翻换假设 + 工具失败 Fallback + 第 15 步强制 escalated
- 终止三出口：conclusion 收束 / 15 步 escalated / 熔断
- 失败模式六值逐值触发（归类字段精确断言）
防绕圈四机制与 EvidenceStep 100% 记录在 test_harness_loop_mechanisms.py。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from oncall.harness.loop import (
    MAX_STEPS,
    FailureMode,
    LoopComponents,
    run_investigation,
)
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import (
    MockPlanner,
    PlannerDecision,
    PlannerOutputError,
    PlannerTimeoutError,
)
from oncall.harness.session import InvestigationSession, SessionStatus
from oncall.harness.tools.registry import (
    ToolRegistry,
    ToolTimeoutError,
    ToolTransportError,
    register_six_tools,
)
from oncall.harness.tools.schemas import ToolResult, ToolStatus
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

_TS = datetime(2026, 9, 8, 8, 0, 0, tzinfo=UTC)


class FakeClock:
    """可注入时钟：测试造不出真实 5min（踩坑⑪），用 advance 推进。"""

    def __init__(self) -> None:
        self._now = _TS

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


def make_handler(tool: str, result: ToolResult | None = None, *, crash: str | None = None) -> Any:
    """工具 handler 夹具：crash="timeout" 触发重试耗尽，crash="transport" 同理。"""

    def handler(args: Any, *, timeout_seconds: float) -> ToolResult:
        if crash == "timeout":
            raise ToolTimeoutError("查询超时")
        if crash == "transport":
            raise ToolTransportError("网络不可达")
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
# G8 防伪三判据
# ---------------------------------------------------------------------------


class TestG8AntiFakeCriteria:
    def test_criterion1_two_scripts_diverge(self) -> None:
        """判据 1：同一 incident 注入两个不同脚本 → 步序列与结论随脚本改变。"""
        script_a = [
            tool_decision(thought="先查指标"),
            tool_decision(
                "search_logs",
                thought="再查日志",
                args={"selector": '{job="api"}', "start": _TS.isoformat()},
            ),
            tool_decision(
                thought="补充查指标",
                args={"promql": "cpu", "start": _TS.isoformat(), "end": _TS.isoformat()},
            ),
            conclusion_decision("脚本 A 结论"),
        ]
        result_a = run_investigation(make_session(), make_components(MockPlanner(script=script_a)))
        result_b = run_investigation(
            make_session(),
            make_components(MockPlanner(script=[tool_decision(thought="一步收束前查一次")])),
        )
        tools_a = [s.tool for s in result_a.steps]
        tools_b = [s.tool for s in result_b.steps]
        assert tools_a == ["query_metrics", "search_logs", "query_metrics"]
        assert tools_b == ["query_metrics"]
        assert result_a.conclusion == "脚本 A 结论"
        assert result_b.conclusion == "mock 默认结论"

    def test_criterion1_decision_input_injectable_and_grows(self) -> None:
        """判据 1 前提：决策输入可注入，且上下文视图随证据步增长。"""
        planner = MockPlanner(script=[tool_decision(), tool_decision(), conclusion_decision()])
        run_investigation(make_session(), make_components(planner))
        sizes = [len(view["steps"]) for view in planner.calls]
        assert sizes == [0, 1, 2]

    def test_criterion2_no_topology_still_concludes(self) -> None:
        """判据 2：SOP 只是工具——全程不调 get_topology 亦正常收束。"""
        script = [
            tool_decision(thought="查指标"),
            tool_decision("search_logs", thought="查日志"),
            tool_decision(
                "search_logs",
                thought="再查日志",
                args={"selector": '{job="api"}', "start": _TS.isoformat()},
            ),
            conclusion_decision("不看拓扑也能收束"),
        ]
        result = run_investigation(make_session(), make_components(MockPlanner(script=script)))
        assert result.status is SessionStatus.CONCLUDED
        assert "get_topology" not in [s.tool for s in result.steps]

    def test_criterion3_hypothesis_rejected_switch(self) -> None:
        """判据 3①：假设被推翻 → Fallback 喂回 → Planner 换新假设。"""
        planner = MockPlanner(
            script=[
                tool_decision(thought="消费者延迟导致堆积"),
                tool_decision(thought="换成 CPU 饱和假设"),
                conclusion_decision("CPU 饱和定案"),
            ]
        )
        judge_script = [VerifierVerdict(supported=False, reason="证据不支持消费者延迟")]
        result = run_investigation(
            make_session(),
            make_components(
                planner,
                judge_script=judge_script,
                judge_default=VerifierVerdict(supported=True, reason="支持"),
            ),
        )
        statuses = {h.text: h.status.value for h in result.hypotheses}
        assert statuses["消费者延迟导致堆积"] == "rejected"
        assert statuses["换成 CPU 饱和假设"] == "confirmed"
        fallback_notices = [n for view in planner.calls for n in view["notices"]]
        assert any("推翻" in n for n in fallback_notices)

    def test_criterion3_tool_failure_fallback(self) -> None:
        """判据 3②：工具重试耗尽 → 结构化失败摘要喂回 Planner 自行换向。"""
        planner = MockPlanner(script=[tool_decision(), conclusion_decision("换向后收束")])
        handlers = dict(DEFAULT_HANDLERS)
        handlers["query_metrics"] = make_handler("query_metrics", crash="timeout")
        result = run_investigation(make_session(), make_components(planner, handlers=handlers))
        assert result.status is SessionStatus.CONCLUDED
        notices = [n for view in planner.calls for n in view["notices"]]
        assert any("重试耗尽" in n for n in notices)
        assert result.failure_mode is FailureMode.PREMATURE_STOP  # 1 步收束且无 confirmed → 预标注

    def test_criterion3_step15_forced_escalated(self) -> None:
        """判据 3③：第 15 步强制 escalated。"""
        script = [
            tool_decision(
                args={"promql": f"m{i}", "start": _TS.isoformat(), "end": _TS.isoformat()}
            )
            for i in range(20)
        ]
        result = run_investigation(make_session(), make_components(MockPlanner(script=script)))
        assert result.status is SessionStatus.ESCALATED
        assert result.step_count == MAX_STEPS


# ---------------------------------------------------------------------------
# M4-05 / T5（D-37）：决策视图 opening 键（loop 接缝）
# ---------------------------------------------------------------------------


class TestDecisionViewOpeningSeam:
    _CARD: ClassVar[dict[str, Any]] = {
        "alert": {
            "labels": {"alertname": "DemoApiGwHighLatency", "job": "api-gw"},
            "source": "alertmanager",
            "status": "deduped",
            "fired_at": "2026-09-06T06:28:21+00:00",
            "last_fired_at": "2026-09-06T06:28:21+00:00",
        },
        "context": {
            "sources": [
                {"source": "metrics", "status": "ok", "items": [], "meta": {}},
            ]
        },
        "generated_at": "2026-09-08T08:00:00Z",
    }

    def test_view_contains_opening_key_default_none(self):
        """run_investigation 不传 opening → view 含 opening 键且为 None（既有 3 剧本回放不炸）。"""
        planner = MockPlanner(script=[conclusion_decision("收束")])
        run_investigation(make_session(), make_components(planner))
        view = planner.calls[0]
        assert set(view) == {"system_prompt", "steps", "hypotheses", "notices", "opening"}
        assert view["opening"] is None

    def test_view_opening_projection_via_injection(self):
        """opening 关键字注入 → 透传至 view（D-37 投影口径由 context_manager 单测钉死）。"""
        planner = MockPlanner(script=[conclusion_decision("收束")])
        run_investigation(make_session(), make_components(planner), opening=self._CARD)
        opening = planner.calls[0]["opening"]
        assert opening["alertname"] == "DemoApiGwHighLatency"
        assert opening["source"] == "alertmanager"
        assert opening["context_status"] == {"metrics": "ok"}


# ---------------------------------------------------------------------------
# 终止三出口
# ---------------------------------------------------------------------------


class TestTerminationExits:
    def test_exit1_conclusion(self) -> None:
        script = [tool_decision(), tool_decision(), conclusion_decision("三步收束")]
        result = run_investigation(make_session(), make_components(MockPlanner(script=script)))
        assert result.status is SessionStatus.CONCLUDED
        assert result.conclusion == "三步收束"

    def test_exit2_step_limit_escalated(self) -> None:
        script = [
            tool_decision(
                args={"promql": f"m{i}", "start": _TS.isoformat(), "end": _TS.isoformat()}
            )
            for i in range(15)
        ]
        result = run_investigation(make_session(), make_components(MockPlanner(script=script)))
        assert result.status is SessionStatus.ESCALATED
        assert result.step_count == 15
        assert result.stop_reason is not None and "15" in result.stop_reason

    def test_exit3_duration_circuit_break_timeout(self) -> None:
        """熔断出口：总时长 >5min → escalate，归类 timeout（时钟经 handler 推进）。"""
        clock = FakeClock()

        def slow_handler(args: Any, *, timeout_seconds: float) -> ToolResult:
            clock.advance(301)  # 单步把总时长推过 5min
            return ToolResult(tool="query_metrics", status=ToolStatus.OK, data={}, meta={})

        handlers = dict(DEFAULT_HANDLERS)
        handlers["query_metrics"] = slow_handler
        script = [tool_decision(), conclusion_decision()]
        result = run_investigation(
            make_session(),
            make_components(MockPlanner(script=script), clock=clock, handlers=handlers),
        )
        assert result.status is SessionStatus.ESCALATED
        assert result.failure_mode is FailureMode.TIMEOUT


# ---------------------------------------------------------------------------
# 失败模式六值归类（逐值触发）
# ---------------------------------------------------------------------------


class TestSixFailureModes:
    def test_tool_error_retries_exhausted_then_escalated(self) -> None:
        """tool_error：重试耗尽后未换向、走到 15 步 → 归类 tool_error。"""
        script = [
            tool_decision(),
            *[
                tool_decision(
                    args={"promql": f"m{i}", "start": _TS.isoformat(), "end": _TS.isoformat()}
                )
                for i in range(20)
            ],
        ]
        handlers = dict(DEFAULT_HANDLERS)
        handlers["query_metrics"] = make_handler("query_metrics", crash="transport")
        result = run_investigation(
            make_session(), make_components(MockPlanner(script=script), handlers=handlers)
        )
        assert result.status is SessionStatus.ESCALATED
        assert result.failure_mode is FailureMode.TOOL_ERROR

    def test_plan_error_planner_output_exhausted(self) -> None:
        """plan_error：Planner 畸形重试 ≤2 耗尽。"""
        script: list[Any] = [
            PlannerOutputError("畸形"),
            PlannerOutputError("畸形"),
            PlannerOutputError("畸形"),
        ]
        result = run_investigation(make_session(), make_components(MockPlanner(script=script)))
        assert result.status is SessionStatus.ESCALATED
        assert result.failure_mode is FailureMode.PLAN_ERROR

    def test_timeout_planner_timeout_no_retry(self) -> None:
        """timeout：Planner 超时 30s 不重试。"""
        script: list[Any] = [PlannerTimeoutError("超时"), conclusion_decision()]
        result = run_investigation(make_session(), make_components(MockPlanner(script=script)))
        assert result.status is SessionStatus.ESCALATED
        assert result.failure_mode is FailureMode.TIMEOUT
        assert result.step_count == 0

    def test_hallucination_conclusion_without_evidence(self) -> None:
        """hallucination：零证据步即收束（规则层检出）。"""
        result = run_investigation(
            make_session(), make_components(MockPlanner(script=[conclusion_decision("无中生有")]))
        )
        assert result.status is SessionStatus.ABORTED
        assert result.failure_mode is FailureMode.HALLUCINATION

    def test_no_signal_all_sources_unavailable(self) -> None:
        """no_signal：三源 unavailable 且无可用证据而收束。"""
        unavailable = ToolResult(
            tool="query_metrics",
            status=ToolStatus.UNAVAILABLE,
            data=None,
            meta={"reason": "不可达"},
        )
        handlers = {
            "query_metrics": make_handler("query_metrics", unavailable),
            "search_logs": make_handler(
                "search_logs",
                ToolResult(
                    tool="search_logs",
                    status=ToolStatus.UNAVAILABLE,
                    data=None,
                    meta={"reason": "不可达"},
                ),
            ),
            "detect_anomaly": make_handler(
                "detect_anomaly",
                ToolResult(
                    tool="detect_anomaly",
                    status=ToolStatus.UNAVAILABLE,
                    data=None,
                    meta={"reason": "不可达"},
                ),
            ),
            "get_topology": make_handler(
                "get_topology",
                ToolResult(
                    tool="get_topology",
                    status=ToolStatus.UNAVAILABLE,
                    data=None,
                    meta={"reason": "不可达"},
                ),
            ),
        }
        script = [
            tool_decision(),
            tool_decision(
                "search_logs",
                thought="查日志",
                args={"selector": '{job="api"}', "start": _TS.isoformat()},
            ),
            tool_decision(
                thought="补查",
                args={"promql": "x", "start": _TS.isoformat(), "end": _TS.isoformat()},
            ),
            tool_decision(
                "detect_anomaly",
                thought="做检测",
                args={"values": [1.0], "timestamps": [_TS.isoformat()]},
            ),
            tool_decision("get_topology", thought="看拓扑"),
            conclusion_decision("没有任何信号"),
        ]
        result = run_investigation(
            make_session(), make_components(MockPlanner(script=script), handlers=handlers)
        )
        assert result.status is SessionStatus.CONCLUDED
        assert result.failure_mode is FailureMode.NO_SIGNAL

    def test_premature_stop_under_five_steps_no_confirmed(self) -> None:
        """premature_stop：步数 <5 且无 confirmed 假设即收束（预标注，不阻断）。"""
        script = [tool_decision(), conclusion_decision("过早收束")]
        result = run_investigation(make_session(), make_components(MockPlanner(script=script)))
        assert result.status is SessionStatus.CONCLUDED
        assert result.failure_mode is FailureMode.PREMATURE_STOP

    def test_normal_conclusion_no_failure_mode(self) -> None:
        """正常收束（≥5 步 + confirmed 假设）→ failure_mode 为 None。"""
        script = [
            tool_decision(thought="假设：消费者延迟"),
            tool_decision(
                "search_logs",
                thought="查日志佐证",
                args={"selector": '{job="api"}', "start": _TS.isoformat()},
            ),
            tool_decision(
                thought="补查指标",
                args={"promql": "cpu", "start": _TS.isoformat(), "end": _TS.isoformat()},
            ),
            tool_decision("get_topology", thought="看拓扑", args={"service": "api-gw"}),
            tool_decision(
                "search_logs",
                thought="再佐证",
                args={"selector": '{job="api"}', "start": _TS.isoformat()},
            ),
            conclusion_decision("证据充分收束"),
        ]
        judge_default = VerifierVerdict(supported=True, reason="证据支持")
        result = run_investigation(
            make_session(), make_components(MockPlanner(script=script), judge_default=judge_default)
        )
        assert result.status is SessionStatus.CONCLUDED
        assert result.failure_mode is None
        assert any(h.status.value == "confirmed" for h in result.hypotheses)
