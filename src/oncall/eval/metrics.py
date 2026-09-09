"""指标核算（m7 issue 04）：eval_runs 行集合 → 五列指标矩阵（纯函数，零 IO）。

口径来源（docs/design/m7-eval-bench-design.md）：
- G3：成本列消费 `cost_cny`（估算合计），与 `usage_log` 实测 usage 分列不混算；
- G4：N=3 报均值±极差；同组判定不一致 → `unstable` 单列不静默平均（D-61）；
  失败模式按多数归类，无多数落 unknown；
- G6：失败模式归类规则链的主消费端——六值由主循环直给（D-28），此处只做
  多数归类与兜底；unknown 禁丢弃，`unknown_requires_human` 置位进人工通道；
- G7/D-64：复用出口不计入 Top-1/Top-3 命中分母（复用命中率单列，D-57）、
  escalated 案例不计入失败（单列，D-56）——降准/漏报不得被复用与 escalated 稀释。

均值±极差的行集：排除 `reused` 行（复用出口无真实调查成本，混入会稀释成本列），
escalated 行真实跑过，计入均值；命中/失败口径见 G7。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from oncall.db.eval_models import EvalRun

#: unknown 强制人工通道标记词（G6：禁丢弃，issue Comments 登记后回填）
UNKNOWN_MODE = "unknown"


def _mean_range(values: Sequence[float]) -> tuple[float | None, float | None]:
    """均值与极差（G4：小样本极差比方差直观）；空集返回 (None, None) 不除零。"""
    if not values:
        return None, None
    return sum(values) / len(values), max(values) - min(values)


def classify_failure_mode(modes: Sequence[str | None]) -> str:
    """失败模式归类规则链末端（G4/G6）：多数归类，无多数落 unknown 禁丢弃。

    六值直消费与 miss-空值兜底在 runner/judging 层完成（G6 优先级一二），
    此处消费已归类的值集合：None（行级未归类）按 unknown 候选参与计数。
    """
    counted = [mode if mode is not None else UNKNOWN_MODE for mode in modes]
    if not counted:
        return UNKNOWN_MODE
    (mode, count), *_ = Counter(counted).most_common(1)
    return mode if count > len(counted) / 2 else UNKNOWN_MODE


def _eligible(row: EvalRun) -> bool:
    """命中分母行谓词（G7/D-64）：复用出口与 escalated 案例均不计入。"""
    return not row.reused and not row.escalated


def compute_run_metrics(rows: Sequence[EvalRun]) -> dict[str, Any]:
    """五列指标矩阵核算：命中 / 步数 / 耗时 / 成本 / 失败模式（纯函数）。

    输入为 eval_runs 行集合（同集合维度由调用方圈定，如单剧本×单模型 N=3
    或全集合）；命中/失败只看 eligible 行（G7），均值±极差排除 reused 行。
    """
    eligible_rows = [row for row in rows if _eligible(row)]
    cost_rows = [row for row in rows if not row.reused]

    top1_hits = sum(1 for row in eligible_rows if row.verdict == "top1")
    top3_hits = sum(1 for row in eligible_rows if row.verdict in ("top1", "top3"))
    miss_rows = [row for row in eligible_rows if row.verdict == "miss"]
    rates_defined = bool(eligible_rows)

    failure_modes: dict[str, int] = Counter(
        row.failure_mode if row.failure_mode is not None else UNKNOWN_MODE for row in miss_rows
    )
    failure_mode = (
        classify_failure_mode([row.failure_mode for row in miss_rows]) if miss_rows else None
    )

    steps_avg, steps_range = _mean_range([float(row.step_count) for row in cost_rows])
    duration_avg, duration_range = _mean_range([row.duration_s for row in cost_rows])
    cost_avg, cost_range = _mean_range([row.cost_cny for row in cost_rows])

    return {
        "total": len(rows),
        "eligible": len(eligible_rows),
        "top1_hits": top1_hits,
        "top3_hits": top3_hits,
        "top1_rate": top1_hits / len(eligible_rows) if rates_defined else None,
        "top3_rate": top3_hits / len(eligible_rows) if rates_defined else None,
        "rates_defined": rates_defined,
        "miss_count": len(miss_rows),
        "reused_count": sum(1 for row in rows if row.reused),  # D-57 单列
        "escalated_count": sum(1 for row in rows if row.escalated),  # D-56 单列
        "steps_avg": steps_avg,
        "steps_range": steps_range,
        "duration_avg": duration_avg,
        "duration_range": duration_range,
        "cost_avg": cost_avg,  # G3：估算列；usage_log 实测列另列不混算
        "cost_range": cost_range,
        "failure_modes": dict(failure_modes),
        "failure_mode": failure_mode,
        "unknown_requires_human": failure_modes.get(UNKNOWN_MODE, 0) > 0,
    }


def summarize_runs(rows: Iterable[EvalRun]) -> list[dict[str, Any]]:
    """按（剧本 × 模型 × 集合）分组做 N=3 汇总（G4/D-61），组序稳定可审计。

    每组：n_runs / unstable（判定不一致单列，不静默平均）+ 五列指标的
    组内均值±极差 + 失败模式多数归类（无多数落 unknown，禁丢弃）。
    """
    groups: dict[tuple[str, str, str], list[EvalRun]] = {}
    for row in rows:
        groups.setdefault((row.scenario, row.model, row.the_set), []).append(row)

    summary: list[dict[str, Any]] = []
    for (scenario, model, the_set), group_rows in sorted(groups.items()):
        metrics = compute_run_metrics(group_rows)
        unstable = len({row.verdict for row in group_rows}) > 1
        summary.append(
            {
                "scenario": scenario,
                "model": model,
                "the_set": the_set,
                "n_runs": len(group_rows),
                "unstable": unstable,
                **metrics,
            }
        )
    return summary
