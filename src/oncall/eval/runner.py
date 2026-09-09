"""剧本 runner（m7 issue 02 / D-58：进程内 harness 直跑，mock/真实双档）。

只装配**既有** LoopComponents 跑 `run_investigation`，消费 `InvestigationResult`
既有列（step_count/total_tokens/total_cost_cny/failure_mode 六值），不改 harness
契约面、不扩 D-23 冻结面。组件装配面（mock 档 MockPlanner/真实档 env profile）
由调用方经 `components_factory` 注入——runner 只编排不装配（架构 §1 总原则）。

双档门槛（D-58，M5 issue 08 `ONCALL_RUN_*` 惯例）：mock 档 CI 可跑零真实调用；
真实档须显式 `ONCALL_RUN_M7_EVAL=1`（`require_real_tier` 门槛，tests skipif 同源）。

落库（D-62）：每遍一行 `eval_runs`（表是明细权威）；判定接缝 `judger` 注入
（规则匹配层归 issue 03，本模块只定义最小 Judgment 结构）；同剧本 N 遍判定
不一致 → `unstable` 单列（D-61，不静默平均）。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from oncall.db.eval_models import EvalRun
from oncall.eval.golden import GoldenScenario
from oncall.harness.loop import InvestigationResult, LoopComponents, run_investigation
from oncall.harness.session import InvestigationSession, SessionStatus

#: 真实档门槛 env（D-58）：显式 `=1` 才允许跑真实 planner（与 tests skipif 同源）
REAL_TIER_ENV = "ONCALL_RUN_M7_EVAL"

#: 每剧本每模型遍数（D-61：N=3，报均值±极差）
N_RUNS = 3


@dataclass(frozen=True)
class Judgment:
    """单遍判定结果（判定接缝最小结构；规则匹配/judge 实现归 issue 03）。"""

    verdict: str  # top1 | top3 | miss（eval_runs CHECK 词汇）
    judged_by: str  # rule | judge | human
    reason: str = ""


@dataclass(frozen=True)
class ScenarioRunSpec:
    """单剧本评测参数（eval_runs 行键：scenario/the_set/model，D-62 契约面）。"""

    scenario: str
    the_set: str  # dev | holdout
    model: str


@dataclass(frozen=True)
class ScenarioCase:
    """单剧本评测用例：golden 标注 + 行键参数成对不可分（防标注与行键错位）。"""

    golden: GoldenScenario
    spec: ScenarioRunSpec


#: 组件装配面：(golden, run_idx) → 既有 LoopComponents（mock/真实由调用方定档）
ComponentsFactory = Callable[[GoldenScenario, int], LoopComponents]

#: 判定接缝：(golden, result) → Judgment（issue 03 规则匹配层即此接缝的实现）
Judger = Callable[[GoldenScenario, InvestigationResult], Judgment]


def require_real_tier() -> None:
    """真实档门槛：env 未显式 `=1` 即 raise（0 漏报纪律：静默 mock 会伪装成真评测）。"""
    if os.environ.get(REAL_TIER_ENV) != "1":
        raise RuntimeError(
            f"真实档未解锁：须显式设 {REAL_TIER_ENV}=1 才允许真实 planner 调查"
            "（D-58 门槛；mock 档无需解锁）"
        )


def _run_once(
    components: LoopComponents, golden: GoldenScenario, run_idx: int
) -> tuple[InvestigationResult, float]:
    """单遍直跑：进程内建会话跑主循环，实测耗时（禁虚构，硬规 10）。

    评测运行无事件锚点（eval_runs 与 investigations 分表不混，D-62），
    会话 incident_id 用 run_idx+1 占位仅供主循环计数，不落 incidents 表。
    """
    session = InvestigationSession(incident_id=run_idx + 1)
    started = time.perf_counter()
    result = run_investigation(session, components)
    return result, time.perf_counter() - started


def _to_row(
    spec: ScenarioRunSpec, run_idx: int, result: InvestigationResult, duration_s: float
) -> EvalRun:
    """InvestigationResult 既有列 → EvalRun 明细行（消费不重造，D-63 优先级一）。"""
    return EvalRun(
        scenario=spec.scenario,
        the_set=spec.the_set,
        model=spec.model,
        run_idx=run_idx,
        verdict="miss",  # 占位：judger 判定后回填，CHECK 词汇内必被覆盖
        failure_mode=result.failure_mode,  # 循环六值直接消费（D-63）
        judged_by="rule",
        step_count=result.step_count,
        duration_s=duration_s,
        tokens=result.total_tokens,
        cost_cny=result.total_cost_cny,
        escalated=result.status is SessionStatus.ESCALATED,  # D-64：单列不计失败
        run_json={
            "conclusion": result.conclusion,
            "stop_reason": result.stop_reason,
            "hypotheses": [{"text": h.text, "status": h.status.value} for h in result.hypotheses],
        },
    )


def run_scenario(
    *,
    case: ScenarioCase,
    components_factory: ComponentsFactory,
    judger: Judger,
    db_session: Session,
    n_runs: int = N_RUNS,
) -> list[EvalRun]:
    """对单剧本跑 N 遍产出 EvalRun 明细（D-58 进程内直跑；D-61 unstable 单列）。"""
    golden, spec = case.golden, case.spec
    rows: list[EvalRun] = []
    for run_idx in range(n_runs):
        try:
            components = components_factory(golden, run_idx)
            result, duration_s = _run_once(components, golden, run_idx)
        except Exception as exc:  # 单格炸不炸全批（禁丢弃：error 行可审计）
            # 2026-09-09 全量实测教训：M6-T5 知识污染防线 VerifierError 中途
            # 炸批回滚 41 分钟真实 spend——每格必须有行，error 痕迹落 run_json
            row = EvalRun(
                scenario=spec.scenario,
                the_set=spec.the_set,
                model=spec.model,
                run_idx=run_idx,
                verdict="miss",  # CHECK 词汇内：异常按 miss 计（judged_by=rule 可追溯）
                failure_mode="tool_error",
                judged_by="rule",
                step_count=0,
                duration_s=0.0,
                tokens=0,
                cost_cny=0.0,
                escalated=False,
                run_json={"error": f"{type(exc).__name__}: {exc}"},
            )
            rows.append(row)
            continue
        row = _to_row(spec, run_idx, result, duration_s)
        judgment = judger(golden, result)
        row.verdict = judgment.verdict
        row.judged_by = judgment.judged_by
        # 判定痕迹持久化（issue 07 冒烟教训：judge 回退 judge_error 痕迹曾丢失，
        # 只能靠探针复现——禁丢弃纪律要求回退可审计）
        row.run_json["judgment"] = {"reason": judgment.reason}
        rows.append(row)
    unstable = len({row.verdict for row in rows}) > 1  # D-61：判定不一致不静默平均
    for row in rows:
        row.unstable = unstable
        db_session.add(row)
    return rows
