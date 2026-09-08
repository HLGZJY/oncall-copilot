"""调查会话契约（D-25：M3 只冻结内存契约不建表，M4 建 ORM 表时契约不倒改）。

- `EvidenceStep`：证据步——一步 thought/tool/input/output 的有序记录单元，
  `output_json`（原始输出，可回溯）与 `output_summary`（进上下文的摘要）双存，
  字段对齐架构 §4 `evidence_steps` 冻结列（id/incident_id 为库列，内存契约不含）
- `Hypothesis`：假设（confirmed/rejected/active），对齐架构 §4 `hypotheses` 冻结列
- `InvestigationSession`：调查会话——M3 的唯一可变状态对象（OpenHands 原则：
  组件不可变、状态单一），序列化即断点恢复工件；M3 内存持有

术语纪律见 CONTEXT.md：调查会话 / Investigation Session、证据步 / Evidence Step、
假设 / Hypothesis；状态四态 running/concluded/escalated/aborted（D-28 终止语义）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EvidenceStep",
    "Hypothesis",
    "HypothesisStatus",
    "InvestigationSession",
    "SessionStatus",
]


class HypothesisStatus(StrEnum):
    """假设三态（CONTEXT.md：假设 / Hypothesis；扩枚举须回 decisions.md 评审）。"""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    ACTIVE = "active"


class SessionStatus(StrEnum):
    """调查会话状态四态（D-28 终止三出口 + aborted）。"""

    RUNNING = "running"
    CONCLUDED = "concluded"
    ESCALATED = "escalated"
    ABORTED = "aborted"


class EvidenceStep(BaseModel):
    """证据步（架构 §4 `evidence_steps` 冻结列的内存契约形态）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_no: int = Field(ge=1, description="步号，从 1 起连续递增")
    thought: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    input_json: dict[str, object]
    output_json: dict[str, object] = Field(description="原始输出（可回溯）")
    output_summary: str = Field(description="进上下文的摘要——与 output_json 双存")
    tokens: int = Field(ge=0)
    cost_cny: float = Field(ge=0.0)
    latency_ms: int = Field(ge=0)
    ts: datetime


class Hypothesis(BaseModel):
    """假设（架构 §4 `hypotheses` 冻结列的内存契约形态）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    supporting_steps: list[int] = Field(default_factory=list)
    against_steps: list[int] = Field(default_factory=list)


class InvestigationSession(BaseModel):
    """调查会话：唯一可变状态对象（incident 锚点 + 步计数 + 状态 + 终止原因 + 集合）。

    组件（EvidenceStep/Hypothesis）frozen、本容器可变——状态变更只经
    `record_step` / `add_hypothesis` / 终态方法，保证步号连续与终态单次迁移。
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    incident_id: int = Field(
        description="事件锚点（incidents.id，M3 以 alert_ids[0] 入口告警调查）"
    )
    status: SessionStatus = SessionStatus.RUNNING
    stop_reason: str | None = None
    steps: list[EvidenceStep] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def _ensure_running(self) -> None:
        if self.status is not SessionStatus.RUNNING:
            msg = f"会话已终态（{self.status.value}），仅 running 态可变更"
            raise ValueError(msg)

    def record_step(self, step: EvidenceStep) -> None:
        """追加证据步并推进步计数；步号必须连续（前一步 +1）。"""
        self._ensure_running()
        expected = self.step_count + 1
        if step.step_no != expected:
            msg = f"step_no 必须连续：期望 {expected}，收到 {step.step_no}"
            raise ValueError(msg)
        self.steps.append(step)

    def add_hypothesis(self, hypothesis: Hypothesis) -> None:
        """入池一条假设（假设-证据步引用存在性校验归 Verifier 规则层，D-26）。"""
        self._ensure_running()
        self.hypotheses.append(hypothesis)

    def _terminate(self, status: SessionStatus, reason: str) -> None:
        self._ensure_running()
        self.status = status
        self.stop_reason = reason

    def conclude(self, reason: str) -> None:
        """Planner `{conclusion}` 正常收束（D-28 终止出口一）。"""
        self._terminate(SessionStatus.CONCLUDED, reason)

    def escalate(self, reason: str) -> None:
        """步数 = 15 或 Harness 熔断时的转人工出口（D-28；不是丢弃）。"""
        self._terminate(SessionStatus.ESCALATED, reason)

    def abort(self, reason: str) -> None:
        """aborted：异常终止（如总时长超限后不可恢复的内部错误）。"""
        self._terminate(SessionStatus.ABORTED, reason)
