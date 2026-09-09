"""M7 第十表 eval_runs（D-62）：从 models.py 拆出（C6 ≤300 行门禁）。

与既有 ORM 模型共用同一 `Base` 元数据——import 本模块即注册进 create_all。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from oncall.db.models import Base


class EvalRun(Base):
    """评测运行（D-62 第十表）：评测台对单个剧本的一次执行明细。

    与 `investigations` 会话记录**分表不混**（CONTEXT.md「评测运行」口径：
    评测行无 incident 外键，不参与调查事务）。判定三值（D-59）、失败模式
    七值（六值 + unknown，D-63）、复用/escalated 单列口径（D-64）均冻结在
    DB 层 CHECK——脏行进不来，M7 矩阵列全部可回溯到本表行（硬规 10）。
    字段契约 = docs/design/m7-eval-bench-design.md §数据模型变更（D-58–D-65）。
    """

    __tablename__ = "eval_runs"
    __table_args__ = (
        CheckConstraint("the_set IN ('dev', 'holdout')", name="ck_eval_runs_the_set"),
        CheckConstraint("verdict IN ('top1', 'top3', 'miss')", name="ck_eval_runs_verdict"),
        CheckConstraint("judged_by IN ('rule', 'judge', 'human')", name="ck_eval_runs_judged_by"),
        CheckConstraint(
            "failure_mode IS NULL OR failure_mode IN "
            "('tool_error', 'plan_error', 'timeout', 'hallucination', "
            "'no_signal', 'premature_stop', 'unknown')",
            name="ck_eval_runs_failure_mode",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 剧本 slug（golden 标注的 scenario，唯一性由加载器守卫保证）
    scenario: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 评测集：dev（开发调参）| holdout（最终评测，显式解锁，D-59 防泄漏）
    the_set: Mapped[str] = mapped_column(String(8), nullable=False)
    # 被评模型 profile 名（env 驱动，零硬编码模型名，D-65）
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    # 同剧本同模型第几遍（0 起；D-61 N=3）
    run_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    # 判定：top1 | top3 | miss（D-59 两级判对错产出）
    verdict: Mapped[str] = mapped_column(String(8), nullable=False)
    # 失败模式：六值 + unknown（D-63）；命中行为 null
    failure_mode: Mapped[str | None] = mapped_column(String(16))
    # 判定来源：rule（规则匹配）| judge（LLM-as-judge）| human（人工抽检/归类）
    judged_by: Mapped[str] = mapped_column(String(8), nullable=False)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_cny: Mapped[float] = mapped_column(Float, nullable=False)
    # D-64 口径分离：复用出口不计命中分母、escalated 不计失败，均单列
    reused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # D-61：同剧本 N 遍判定不一致 → unstable 单列不静默平均
    unstable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 明细 JSON（证据步摘要/假设终态等，可回溯扩展面；权威数字在上列）
    run_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
