"""M3-01 Planner 接缝测试（D-22：JSON Mode + Pydantic 契约，异常族 harness 自持）。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from oncall.harness.planner import (
    MockPlanner,
    PlannerClient,
    PlannerDecision,
    PlannerError,
    PlannerOutputError,
    PlannerTimeoutError,
)


def tool_decision(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "thought": "先查队列延迟指标",
        "next_tool": "query_metrics",
        "args": {"query": "queue_lag"},
    }
    payload.update(overrides)
    return payload


class TestPlannerDecision:
    def test_tool_branch_valid(self) -> None:
        d = PlannerDecision.model_validate(tool_decision())
        assert d.next_tool == "query_metrics" and d.conclusion is None

    def test_conclusion_branch_valid(self) -> None:
        d = PlannerDecision.model_validate({"thought": "已证实", "conclusion": "慢 SQL"})
        assert d.conclusion == "慢 SQL" and d.next_tool is None and d.args is None

    def test_both_branches_rejected(self) -> None:
        with pytest.raises(ValidationError, match="互斥"):
            PlannerDecision.model_validate(tool_decision(conclusion="慢 SQL"))

    def test_neither_branch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="二选一"):
            PlannerDecision.model_validate({"thought": "只有 thought"})

    def test_tool_branch_requires_args(self) -> None:
        payload = tool_decision()
        payload["args"] = None
        with pytest.raises(ValidationError, match="args"):
            PlannerDecision.model_validate(payload)

    def test_args_non_dict_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlannerDecision.model_validate(tool_decision(args=["not", "a", "dict"]))

    def test_empty_tool_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlannerDecision.model_validate(tool_decision(next_tool=""))

    def test_empty_thought_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlannerDecision.model_validate(tool_decision(thought=""))

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlannerDecision.model_validate(tool_decision(action="extra"))

    def test_frozen(self) -> None:
        d = PlannerDecision.model_validate(tool_decision())
        with pytest.raises(ValidationError):
            d.next_tool = "search_logs"  # type: ignore[misc]


class TestExceptionFamily:
    def test_hierarchy(self) -> None:
        """异常族 harness 自持（C3 禁 import classify），语义对齐 M2 同名异常。"""
        assert issubclass(PlannerOutputError, PlannerError)
        assert issubclass(PlannerTimeoutError, PlannerError)

    def test_mock_can_raise_scripted_exceptions(self) -> None:
        with pytest.raises(PlannerOutputError):
            MockPlanner(script=[PlannerOutputError("畸形")]).decide({})
        with pytest.raises(PlannerTimeoutError):
            MockPlanner(script=[PlannerTimeoutError("30s 超时")]).decide({})


class TestMockPlanner:
    def test_is_planner_client(self) -> None:
        assert isinstance(MockPlanner(), PlannerClient)

    def test_script_replay_in_order_mixed(self) -> None:
        d1 = PlannerDecision.model_validate(tool_decision())
        err = PlannerOutputError("畸形")
        d2 = PlannerDecision.model_validate({"thought": "收束", "conclusion": "队列堆积"})
        mock = MockPlanner(script=[d1, err, d2])
        assert mock.decide({"k": 1}) is d1
        with pytest.raises(PlannerOutputError):
            mock.decide({"k": 2})
        assert mock.decide({"k": 3}) is d2

    def test_exhausted_falls_back_to_default_conclusion(self) -> None:
        mock = MockPlanner()
        for _ in range(3):
            d = mock.decide({})
            assert d.next_tool is None
            assert d.conclusion == "mock 默认结论"

    def test_default_conclusion_customizable(self) -> None:
        mock = MockPlanner(conclusion="根因是 CPU 飙高")
        assert mock.decide({}).conclusion == "根因是 CPU 飙高"

    def test_calls_recorded(self) -> None:
        mock = MockPlanner()
        view = {"incident_id": 1, "steps": []}
        mock.decide(view)
        assert mock.calls == [view]
