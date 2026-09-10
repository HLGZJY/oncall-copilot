"""M3 issue 09：planner 取证策略升级（先取证后 KB）+ 调查视图事件锚点。

M7 issue 07 全 miss 复盘修复票的红绿测试：
- 系统提示取证策略：先取证后 KB——首轮消费事件锚点与取证工具输出，
  query_kb 降级为取证后参考召回，禁止假设仅由 query_kb 支撑（M6-T5 对齐）；
- 事件锚点进视图：opening 可携带 `event_anchors`（复用 eval/evidence.py
  timeline 形态），缺省不带键（D-37 键集合稳定契约不破坏）；
- eval runner 注入：`_run_once` 由 golden 告警时间线构建 opening（D-18 同源，
  零 root_cause 泄漏）传给 run_investigation；
- 真实 client 透传：`_messages_of` 把 opening 放进 user 视图（此前 opening
  根本到不了模型——M7 issue 07 根因链实锤的一环）；
- M6-T5 交互回归：取证证据支撑假设 → Verifier 正常证实；仅 KB → 拦截
  （既有 test_kb_verifier_guard 保持绿）。
零真实 LLM 调用、零网络外呼。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.eval.golden import GoldenScenario
from oncall.eval.runner import Judgment, ScenarioCase, ScenarioRunSpec, run_scenario
from oncall.harness.context_manager import build_system_prompt, estimate_tokens, project_opening
from oncall.harness.loop import LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner, PlannerDecision
from oncall.harness.session import (
    EvidenceStep,
    Hypothesis,
    HypothesisStatus,
    InvestigationSession,
)
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.tools.schemas import ToolResult, ToolStatus
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierError, VerifierVerdict
from oncall.infra.llm import LLMClientConfig
from oncall.infra.llm_planner import _messages_of

# ── ① 系统提示取证策略 ────────────────────────────────────────────────


class TestForensicsFirstStrategy:
    def test_prompt_contains_forensics_first_strategy(self):
        """系统提示含取证策略：先取证后 KB + KB 降级参考召回 + 禁 KB-only 假设。"""
        prompt = build_system_prompt()
        assert "先取证后 KB" in prompt
        assert "query_kb" in prompt
        assert "参考召回" in prompt
        assert "仅由 query_kb 支撑" in prompt

    def test_prompt_still_within_budget(self):
        """策略升级后整体仍 ≤1500 tokens（预算断言内建，额外显式核对口径）。"""
        prompt = build_system_prompt()
        assert estimate_tokens(prompt) <= 1500


# ── ② opening 事件锚点投影 ────────────────────────────────────────────

_ANCHORS = {
    "runs": [{"started_at": "2026-09-06T06:28:00+00:00", "recovered_at": None}],
    "alerts": [
        {
            "alert_name": "DemoApiGwHighLatency",
            "labels": {"alertname": "DemoApiGwHighLatency", "job": "api-gw"},
            "fired_at": "2026-09-06T06:28:21+00:00",
            "resolved_at": "2026-09-06T06:31:00+00:00",
        }
    ],
}


class TestOpeningEventAnchors:
    def test_event_anchors_projected_when_present(self):
        """opening 携带 event_anchors → 原样进投影（复用 golden timeline 形态）。"""
        opening = project_opening({"alert": {}, "context": {}, "event_anchors": _ANCHORS})
        assert opening is not None
        assert opening["event_anchors"] == _ANCHORS

    def test_projection_without_anchors_keeps_key_set_stable(self):
        """D-17 卡片无 event_anchors → 投影不带该键（既有键集合契约不破坏）。"""
        opening = project_opening({"alert": {"labels": {"alertname": "X"}}, "context": {}})
        assert opening is not None
        assert "event_anchors" not in opening

    def test_none_opening_still_none(self):
        assert project_opening(None) is None


# ── ③ eval runner 注入事件锚点 ────────────────────────────────────────


def _golden_with_timeline() -> GoldenScenario:
    return GoldenScenario(
        scenario="cpu-spike",
        root_cause="cpu 饱和导致事件循环饥饿",
        remediation="扩容并限流",
        runs=[
            {
                "started_at": "2026-09-06T06:28:00+00:00",
                "recovered_at": "2026-09-06T06:31:00+00:00",
                "alert_timeline": [
                    {
                        "alert_name": "DemoApiGwHighLatency",
                        "labels": {"alertname": "DemoApiGwHighLatency", "job": "api-gw"},
                        "fired_at": "2026-09-06T06:28:21+00:00",
                        "resolved_at": "2026-09-06T06:29:00+00:00",
                    }
                ],
            }
        ],
    )


_EXPECTED_ALERTS = [
    {
        "alert_name": "DemoApiGwHighLatency",
        "labels": {"alertname": "DemoApiGwHighLatency", "job": "api-gw"},
        "fired_at": "2026-09-06T06:28:21+00:00",
        "resolved_at": "2026-09-06T06:29:00+00:00",
    }
]

_OK_ARGS = {
    "promql": "up",
    "start": "2026-09-06T06:28:21+00:00",
    "end": "2026-09-06T06:29:21+00:00",
}


def _ok_handler(args: Any, *, timeout_seconds: float) -> ToolResult:
    """取证替身：即时返回 OK（零真实 IO）。"""
    return ToolResult(tool="query_metrics", status=ToolStatus.OK, data={"direction": "up"}, meta={})


def _forensic_components(planner: Any, *, verifier: Any) -> LoopComponents:
    """MockPlanner + 四取证工具替身的最小 LoopComponents。"""
    registry = ToolRegistry(now=lambda: datetime.now(UTC))
    register_six_tools(
        registry,
        dict.fromkeys(
            ("query_metrics", "search_logs", "detect_anomaly", "get_topology"),
            _ok_handler,
        ),
    )
    return LoopComponents(
        planner=planner,
        registry=registry,
        gate=PermissionGate(now=lambda: datetime.now(UTC)),
        verifier=verifier,
        now=lambda: datetime.now(UTC),
    )


def _forensic_then_conclude_planner(conclusion: str) -> MockPlanner:
    """剧本：先取证一步（合法入参）→ 依据取证收束（零证据收束会被规则层拦）。"""
    return MockPlanner(
        script=[
            PlannerDecision.model_validate(
                {
                    "thought": "先取证查指标",
                    "next_tool": "query_metrics",
                    "args": dict(_OK_ARGS),
                }
            ),
            PlannerDecision.model_validate({"thought": "依据取证收束", "conclusion": conclusion}),
        ]
    )


def _supported_verifier() -> Verifier:
    return Verifier(
        judge=MockVerifierJudge(
            default=VerifierVerdict(supported=True, reason="证据支持（裁决接缝留 mock）")
        )
    )


class TestRunnerInjectsEventAnchors:
    def test_run_once_view_contains_golden_timeline_anchors(self):
        """runner 直跑把 golden 告警时间线作为 opening.event_anchors 注入 planner 视图。"""
        from oncall.eval import runner as runner_mod  # noqa: PLC0415 私有面按需引用

        planner = _forensic_then_conclude_planner("结论")
        result, _ = runner_mod._run_once(
            _forensic_components(planner, verifier=_supported_verifier()),
            _golden_with_timeline(),
            0,
        )
        assert result.conclusion == "结论"
        anchors = planner.calls[0]["opening"]["event_anchors"]
        assert anchors["alerts"] == _EXPECTED_ALERTS
        assert anchors["runs"][0]["started_at"] == "2026-09-06T06:28:00+00:00"

    def test_anchors_carry_no_annotation_leak(self):
        """事件锚点零标注泄漏：root_cause / remediation / investigation_path 不进视图。"""
        from oncall.eval import runner as runner_mod  # noqa: PLC0415 私有面按需引用

        planner = _forensic_then_conclude_planner("c")
        runner_mod._run_once(
            _forensic_components(planner, verifier=_supported_verifier()),
            _golden_with_timeline(),
            0,
        )
        text = str(planner.calls[0]["opening"])
        assert "cpu 饱和" not in text
        assert "扩容" not in text
        assert "investigation_path" not in text

    def test_run_scenario_end_to_end_with_anchors(self):
        """run_scenario 全链路：MockPlanner 记到的视图含 opening 事件锚点。"""
        engine = sqlalchemy.create_engine("sqlite://", connect_args={"check_same_thread": False})
        create_tables(engine)
        db = Session(engine)

        def factory(golden: GoldenScenario, run_idx: int) -> LoopComponents:
            return _forensic_components(
                _forensic_then_conclude_planner("cpu 饱和导致事件循环饥饿"),
                verifier=_supported_verifier(),
            )

        golden = _golden_with_timeline()
        case = ScenarioCase(
            golden=golden,
            spec=ScenarioRunSpec(scenario=golden.scenario, the_set="dev", model="mock"),
        )
        rows = run_scenario(
            case=case,
            components_factory=factory,
            judger=lambda g, r: Judgment(verdict="top1", judged_by="rule", reason="ok"),
            db_session=db,
            n_runs=1,
        )
        assert rows[0].verdict == "top1"


# ── ④ 真实 client 透传 opening ────────────────────────────────────────


class TestRealClientCarriesOpening:
    def test_messages_include_opening_in_user_view(self):
        """_messages_of 把 opening 放进 user 视图（事件锚点必须到达模型）。"""
        view = {
            "system_prompt": "sys",
            "steps": [],
            "hypotheses": [],
            "notices": [],
            "opening": {"event_anchors": _ANCHORS},
        }
        messages = _messages_of(view)
        assert messages[0]["role"] == "system"
        user = messages[1]["content"]
        assert '"opening"' in user
        assert "event_anchors" in user
        assert "DemoApiGwHighLatency" in user

    def test_config_from_env_unchanged(self):
        """装配面回归：LLMClientConfig.from_env 词汇不变（接缝不破坏）。"""
        config = LLMClientConfig.from_env(
            {"ONCALL_LLM_BASE_URL": "http://x", "ONCALL_LLM_MODEL": "m", "ONCALL_LLM_API_KEY": "k"}
        )
        assert config.model == "m"


# ── ⑤ M6-T5 交互回归：取证证实 / 仅 KB 拦截 ──────────────────────────


def _session_with_step(tool: str) -> InvestigationSession:
    session = InvestigationSession(incident_id=1)
    session.steps.append(
        EvidenceStep(
            step_no=1,
            thought="取证",
            tool=tool,
            input_json={},
            output_json={"status": "ok", "direction": "up"},
            output_summary="组件=a｜指标=b｜异常方向=up｜时间窗=n/a",
            tokens=1,
            cost_cny=0.0,
            latency_ms=1,
            ts=datetime(2026, 9, 10, tzinfo=UTC),
        )
    )
    return session


class _FixedJudge:
    """最小裁决替身：固定返回指定裁决。"""

    def __init__(self, *, supported: bool) -> None:
        self._verdict = VerifierVerdict(
            supported=supported, reason="依据证据步判定" if supported else "证据不足"
        )

    def judge(self, context_view: Any) -> VerifierVerdict:
        return self._verdict


class TestForensicEvidenceConfirmPath:
    def test_forensic_supported_hypothesis_confirmed(self):
        """取证工具（query_metrics）证据支撑假设 + 裁决支持 → 正常证实，无 VerifierError。"""
        session = _session_with_step("query_metrics")
        hypothesis = Hypothesis(text="指标异常证实假设", supporting_steps=[1])
        session.add_hypothesis(hypothesis)
        verifier = Verifier(judge=_FixedJudge(supported=True))
        outcome = verifier.request_judgment(session, hypothesis, timing="hypothesis")
        assert outcome.verdict is not None and outcome.verdict.supported is True
        updated = verifier.apply_verdict(session, hypothesis, outcome.verdict)
        assert updated.status is HypothesisStatus.CONFIRMED

    def test_kb_only_supported_hypothesis_still_blocked(self):
        """仅 KB 支撑 → 拦截不变（M6-T5 防线②保持）。"""
        session = _session_with_step("query_kb")
        hypothesis = Hypothesis(text="kb 历史相似案例假设", supporting_steps=[1])
        session.add_hypothesis(hypothesis)
        verifier = Verifier(judge=_FixedJudge(supported=True))
        outcome = verifier.request_judgment(session, hypothesis, timing="hypothesis")
        assert outcome.verdict is not None
        with pytest.raises(VerifierError, match="query_kb"):
            verifier.apply_verdict(session, hypothesis, outcome.verdict)

    def test_mixed_support_confirmed(self):
        """KB + 取证共同支撑 → 不拦（防线只拦「仅 KB」）。"""
        session = _session_with_step("query_metrics")
        session.steps.append(
            EvidenceStep(
                step_no=2,
                thought="参考召回",
                tool="query_kb",
                input_json={},
                output_json={"status": "ok"},
                output_summary="参考",
                tokens=1,
                cost_cny=0.0,
                latency_ms=1,
                ts=datetime(2026, 9, 10, tzinfo=UTC),
            )
        )
        hypothesis = Hypothesis(text="混合支撑假设", supporting_steps=[1, 2])
        session.add_hypothesis(hypothesis)
        verifier = Verifier(judge=_FixedJudge(supported=True))
        outcome = verifier.request_judgment(session, hypothesis, timing="hypothesis")
        assert outcome.verdict is not None
        updated = verifier.apply_verdict(session, hypothesis, outcome.verdict)
        assert updated.status is HypothesisStatus.CONFIRMED
