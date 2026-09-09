"""M5-T7 验收断言收口 · D-23/D-25 冻结面与状态机（m5 issue 07）。

验收「D-23 冻结面不破」的收口断言（照 M4 先例：import + 键集合精确断言，
不用「存在性」软断言）：
- 六工具集合 / ExecuteActionInput{action, params} / ToolResult 形状精确钉死
- D-25：EvidenceStep/Hypothesis ORM 冻结列 + InvestigationSession 字段不倒改
- 状态机迁移表键集合：pending 无执行出边、终态零出边（「写操作 100% 过
  确认门」的机制面——未经 approve 的 proposal 不可能进入执行面）
"""

from __future__ import annotations

from oncall.db.models import EvidenceStep, Hypothesis
from oncall.harness.session import InvestigationSession
from oncall.harness.tools.registry import TOOL_NAMES
from oncall.harness.tools.schemas import ExecuteActionInput, ToolResult
from oncall.remediation import service

# D-23 冻结面（只消费不推翻：六工具 / 入参与返回形状）
SIX_TOOLS = {
    "query_metrics",
    "search_logs",
    "detect_anomaly",
    "get_topology",
    "query_kb",
    "execute_action",
}

# D-25 冻结契约（架构 §4 列集 + M3 内存契约字段）
EVIDENCE_STEP_COLUMNS = {
    "id",
    "incident_id",
    "step_no",
    "thought",
    "tool",
    "input_json",
    "output_json",
    "output_summary",
    "tokens",
    "cost",
    "latency_ms",
    "ts",
}
HYPOTHESIS_COLUMNS = {"id", "incident_id", "text", "status", "supporting_steps", "against_steps"}
SESSION_FIELDS = {"incident_id", "status", "stop_reason", "steps", "hypotheses"}


def test_d23_frozen_tool_face_unchanged():
    """D-23：六工具集合、ExecuteActionInput{action, params}、ToolResult 形状精确钉死。"""
    assert set(TOOL_NAMES) == SIX_TOOLS
    assert set(ExecuteActionInput.model_fields) == {"action", "params"}
    assert set(ToolResult.model_fields) == {"tool", "status", "data", "meta"}


def test_d25_frozen_contracts_unchanged():
    """D-25：EvidenceStep/Hypothesis ORM 冻结列 + InvestigationSession 字段不倒改。"""
    assert set(EvidenceStep.__table__.columns.keys()) == EVIDENCE_STEP_COLUMNS
    assert set(Hypothesis.__table__.columns.keys()) == HYPOTHESIS_COLUMNS
    assert set(InvestigationSession.model_fields) == SESSION_FIELDS


def test_state_machine_no_unapproved_execution_edge():
    """验收「100% 过确认门」：迁移表键集合断言——pending 无执行出边、终态零出边
    （单一权威 = service._TRANSITIONS；执行只可经 approved → executing）。"""
    transitions = service._TRANSITIONS
    assert set(transitions) == set(service.PROPOSAL_STATES)
    assert transitions["pending"] == frozenset({"approved", "rejected"})  # 无 executing 出边
    assert transitions["approved"] == frozenset({"executing"})
    for terminal in service.TERMINAL_STATES:
        assert transitions[terminal] == frozenset()
