"""M3 调查循环 harness（T1 起步：契约与 Planner 接缝，六组件后续票逐个落地）。

公开面（issue 01）：
- `InvestigationSession` / `EvidenceStep` / `Hypothesis` / `HypothesisStatus` /
  `SessionStatus` —— 调查会话契约（session.py，D-25 内存契约不建表）
- `PlannerClient` / `PlannerDecision` / `MockPlanner` /
  `PlannerError` / `PlannerOutputError` / `PlannerTimeoutError`
  —— Planner 接缝（planner.py，D-22；异常族 harness 自持，语义对齐
  oncall.classify.client 的 LLMOutputError/LLMTimeoutError）

架构边界（C3）：本包禁止 import oncall.ingest / classify / remediation /
knowledge / eval / api。
"""

from oncall.harness.planner import (
    MockPlanner,
    PlannerClient,
    PlannerDecision,
    PlannerError,
    PlannerOutputError,
    PlannerTimeoutError,
)
from oncall.harness.session import (
    EvidenceStep,
    Hypothesis,
    HypothesisStatus,
    InvestigationSession,
    SessionStatus,
)

__all__ = [
    "EvidenceStep",
    "Hypothesis",
    "HypothesisStatus",
    "InvestigationSession",
    "MockPlanner",
    "PlannerClient",
    "PlannerDecision",
    "PlannerError",
    "PlannerOutputError",
    "PlannerTimeoutError",
    "SessionStatus",
]
