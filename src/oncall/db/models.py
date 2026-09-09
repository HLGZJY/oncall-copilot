"""SQLAlchemy ORM 模型（M1–M6 主表；M7 第十表 eval_runs 拆在 `eval_models`，C6 门禁）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates


class Base(DeclarativeBase):
    """全项目共用 declarative base（M2+ 建表沿用，便于 create_all/Alembic 统一元数据）。"""


class AlertEvent(Base):
    """一条归一化告警：M1 指纹去重后落库，M2 消费（status: deduped → classified）。

    字段契约 = 架构 §4 冻结列 + D-13 M1 增列
    （docs/architecture/architecture.md §4、docs/design/decisions.md D-13）。
    字段一旦被 M2+ 引用难以改名，改动须过 decisions.md 评审。
    """

    __tablename__ = "alert_events"
    __table_args__ = (
        # 架构 §4 冻结 status 取值；CHECK 落 DB 层而非应用层，脏数据进不来
        CheckConstraint("status IN ('deduped', 'classified')", name="ck_alert_events_status"),
    )

    # ── 架构 §4 冻结列 ──
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 指纹 = canonical 稳定 label 子集 sha256 hex（G1，计算逻辑在 T3 应用层）；
    # 唯一约束即去重锚点：同指纹重复 firing 走行内合并（G2）而非新增行
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    labels_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="deduped")

    # ── D-13 M1 增列 ──
    # 首次 firing 计 1，窗口内重复由应用层 ++（M7 降噪统计分母）
    dedup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 每次重复 firing 前移；首次落库由 fired_at 回填
    last_fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 原始告警 annotations + Alertmanager 自带 fingerprint（全 labels FNV-1a，易变，
    # 不作主指纹，仅交叉溯源，见 G5/D-13）
    annotations_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # ── D-19 M2 增列 ──
    # 分类审计全量（ClassificationResult 八键序列化；NULL = 未分类）。
    # verdict 查询走 SQLite JSON1 json_extract（G4/R7：11 剧本规模不预优化，
    # 不建独立 verdict 列/索引，实测成为瓶颈再议）
    classification_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    @validates("fired_at")
    def _backfill_last_fired(self, key: str, value: datetime) -> datetime:
        """首次落库 last_fired_at == fired_at；显式传入值不覆盖。"""
        if self.last_fired_at is None:
            self.last_fired_at = value
        return value


class Incident(Base):
    """M2 最小事件集（G5 定案，decisions.md D-19）：真实告警 1:1 建档。

    - `alert_ids` 是 JSON 数组（M2 恒为单元素，`alert_ids[0]` 即 primary anchor，
      M3 取证从该行的 D-17 事件卡片开局）；数组结构为跨告警归并预留，
      聚合本身是 M3 的 Non-goal；
    - `severity` 取告警 `labels.severity`，缺省 warning（应用层兜底）；
    - `status` 枚举照架构 §4 冻结（investigating/mitigated/closed），
      M2 只产 investigating，流转归 M3/M4。
    """

    __tablename__ = "incidents"
    __table_args__ = (
        # 架构 §4 冻结 status 取值；CHECK 落 DB 层而非应用层（与 alert_events 同款）
        CheckConstraint(
            "status IN ('investigating', 'mitigated', 'closed')", name="ck_incidents_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="investigating")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Investigation(Base):
    """调查记录（D-31 第七表）：一次调查的会话级落库，incident 1:1 覆盖语义。

    会话级字段（终态/stop_reason/结论/failure_mode/步数/成本合计）落此——
    从证据表推导是伪权威（D-31），成本会话级汇总不做步级摊销（D-34，G5 定案）；
    `incident_id` 唯一约束 = 同 incident 重复调查覆盖旧行，对齐现进程内注册表
    覆盖语义（D-31）；历史多次调查归 M7 `eval_runs` 另表，不与此混表。
    字段契约 = docs/design/m4-evidence-chain-design.md §数据模型变更（D-30/D-31/D-32）。
    """

    __tablename__ = "investigations"
    __table_args__ = (
        # D-28 会话状态四态；CHECK 落 DB 层而非应用层（照 ck_alert_events_status 先例）
        CheckConstraint(
            "status IN ('running', 'concluded', 'escalated', 'aborted')",
            name="ck_investigations_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 一对多「一」端锚 incidents.id（D-19 五字段冻结，本表不反向扩列）；
    # 唯一约束即重复调查覆盖锚点（D-31）
    incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("incidents.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # D-28 终止出口描述，终态时落（running 态为 NULL）
    stop_reason: Mapped[str | None] = mapped_column(Text)
    # Planner `{conclusion}` 收束结论（D-22 协议出口一）
    conclusion: Mapped[str | None] = mapped_column(Text)
    # D-28 failure_mode 六值机械归类（tool_error/plan_error/timeout/hallucination/
    # no_signal/premature_stop），M7 评测矩阵列直接来源；running 态为 NULL
    failure_mode: Mapped[str | None] = mapped_column(String(32))
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 会话级汇总（D-34）= Planner usage_log + Verifier 裁决 usage（D-28 收尾结构 total 两字段）
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_cny: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # M4-T3（D-35/D-31）：opening_card 随行留存（D-17 卡片 JSON，收尾后写）——
    # 读路径零重建（卡内 generated_at 是构建时刻时间戳，读时重建必然漂移，
    # roundtrip 逐字段对账不成立）；D-19 不受影响（incidents 表不扩列）
    opening_card_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class EvidenceStep(Base):
    """证据步（架构 §4 冻结列）：调查中一步 thought/tool/input/output 的有序记录单元。

    `output_json`（原始输出，可回溯）与 `output_summary`（进上下文的摘要）双存
    （Anthropic："不能只存摘要"，架构 §4 设计约束）；input 侧只落 `input_json`
    全文，不设 `input_summary` 列（D-32）；(incident_id, step_no) 组合唯一——
    步号在单次调查内连续（内存契约 record_step 连续性校验的 DB 层兜底）。
    """

    __tablename__ = "evidence_steps"
    __table_args__ = (
        UniqueConstraint("incident_id", "step_no", name="uq_evidence_steps_incident_step"),
    )

    # ── 架构 §4 冻结列（字段名与顺序逐字段照抄，D-25）──
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(Integer, ForeignKey("incidents.id"), nullable=False)
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 步号从 1 起连续递增
    thought: Mapped[str] = mapped_column(Text, nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    # D-32：input 只落全文；output 双存照架构 §4 冻结
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_summary: Mapped[str] = mapped_column(Text, nullable=False)
    # 步级 tokens/cost 维持工具埋点现语义（D-34，不做步级摊销）；列名 `cost` 照 §4 冻结
    tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost: Mapped[float] = mapped_column(Float, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RemediationProposal(Base):
    """处置提案（D-46 第八表）：一次处置提案一行，锚 incident 一对多（可多次处置尝试）。

    `dry_run_json` 是批准对象（D-39）：创建即锁定「将被批准执行的具体命令清单 +
    影响面」，状态机任何路径不改写它，confirm/reject 用此渲染；
    `investigation_id` 可空——无产出调查的处置也可建行（G8）；
    生命周期跨调查（pending 等确认发生在调查收尾后），故不混入 evidence_steps
    （锚调查步）/ investigations（会话级冻结，D-31）。
    字段契约 = docs/design/m5-remediation-gates-design.md §数据模型变更（D-46）。
    """

    __tablename__ = "remediation_proposals"
    __table_args__ = (
        # D-46/D-40 八值状态集；CHECK 落 DB 层而非应用层（照 ck_incidents_status 先例）
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executing', "
            "'recovered', 'failed', 'rolled_back', 'escalated')",
            name="ck_remediation_proposals_status",
        ),
        # confirm 落点仅 approve/reject 或 NULL（可选 CHECK，票面建议采纳）
        CheckConstraint(
            "(decision IN ('approve', 'reject')) OR decision IS NULL",
            name="ck_remediation_proposals_decision",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 锚 incident（一对多，不加唯一约束——一次事故可多次处置尝试）；索引供
    # `GET /remediations?incident_id=` 按事故查询
    incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("incidents.id"), nullable=False, index=True
    )
    # 产出调查可空：无调查直接处置的路径（G8）
    investigation_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("investigations.id"))
    runbook_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    action_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # 批准对象（D-39）：创建即有、全路径不可变（验收⑤）
    dry_run_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # 执行参数/上下文（05/06 写）；本票保证可落可读
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # confirm 落点（D-40：approve/reject）
    decision: Mapped[str | None] = mapped_column(String(16))
    confirm_reason: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 恢复验证结果（06 写）
    verify_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rollback_status: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Hypothesis(Base):
    """假设（架构 §4 冻结列）：confirmed/rejected/active 三态（D-26 证伪导向）。

    `supporting_steps`/`against_steps` 为证据步号数组（JSON，SQLite 方言），
    引用存在性校验归 Verifier 规则层（D-26），DB 层不做跨表步号校验。
    """

    __tablename__ = "hypotheses"
    __table_args__ = (
        # 三态冻结（对齐 M3 HypothesisStatus）；CHECK 落 DB 层（同款先例）
        CheckConstraint(
            "status IN ('confirmed', 'rejected', 'active')", name="ck_hypotheses_status"
        ),
    )

    # ── 架构 §4 冻结列（字段名与顺序逐字段照抄，D-25）──
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(Integer, ForeignKey("incidents.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    supporting_steps: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    against_steps: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)


class KbChunk(Base):
    """知识块（D-55 第九表）：闭环报告按章节切出的入库单元，锚 incident 一对多。

    文本权威在本表（可 SQL 审计、可 join incidents 回溯），Chroma 只存可重建的
    向量索引（D-52/G7）；入库门槛 = incident `mitigated` 实证（D-56，知识污染
    第一道防线），未实证事件没有入库通道；`superseded_at` 非空 = 旧块已被
    重复调查覆盖淘汰（D-31 覆盖语义联动，召回不再命中）；`hit_count` 只计真实
    向量召回、不计缓存复用（D-57，M7 召回质量口径分离）。
    字段契约 = docs/design/m6-report-kb-design.md §数据模型变更（D-49–D-57）。
    """

    __tablename__ = "kb_chunks"
    __table_args__ = (
        # D-53 五类 section 冻结；CHECK 落 DB 层（照 ck_incidents_status 先例）
        CheckConstraint(
            "section IN ('opening_card', 'timeline', 'root_cause', 'remediation', 'suggestions')",
            name="ck_kb_chunks_section",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 锚 incident（一对多：一次闭环报告切多块）；索引供按事故查询
    incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("incidents.id"), nullable=False, index=True
    )
    # 产出调查可空：缓存复用路径无新调查（D-57 reused_from）
    investigation_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("investigations.id"))
    section: Mapped[str] = mapped_column(String(16), nullable=False)
    # 同 section 内块序（从 0 起递增）
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 块文本（权威存储；切块器产出，禁虚构——每个数据点可经 source_meta_json 回溯）
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # 拼装来源锚点：报告版本/生成时间/各数据点来源表与行 id（D-49 可回溯）
    source_meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # 命中计数（真实向量召回 ++；缓存复用不计，D-57）；M7 低命中率淘汰反馈接口预留
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    # 覆盖淘汰标记：非空 = 已被重复调查的新块取代（召回不再命中）
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
