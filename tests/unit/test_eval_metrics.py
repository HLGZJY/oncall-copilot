"""M7 issue 04 验收 · 指标核算（五列矩阵 + 复用/escalated 单列，D-56/D-57/D-64）。

纯函数核算：输入 eval_runs 行集合 → 输出指标字典，零 IO、零真实调用。
口径来源：G3 成本两列分列、G4 N=3 均值±极差 / unstable / 失败模式多数归类、
G6 规则链 + unknown 强制人工、G7 复用出口不计命中分母 / escalated 不计失败。
"""

from __future__ import annotations

from oncall.db.eval_models import EvalRun
from oncall.eval.metrics import classify_failure_mode, compute_run_metrics, summarize_runs


def _row(  # noqa: PLR0913
    # 测试夹具需要全关键字默认值，参数多为合法口径断言面
    *,
    scenario: str = "cpu-spike",
    model: str = "mock",
    verdict: str = "top1",
    failure_mode: str | None = None,
    step_count: int = 3,
    duration_s: float = 1.0,
    cost_cny: float = 0.1,
    reused: bool = False,
    escalated: bool = False,
    the_set: str = "dev",
    run_idx: int = 0,
) -> EvalRun:
    return EvalRun(
        scenario=scenario,
        the_set=the_set,
        model=model,
        run_idx=run_idx,
        verdict=verdict,
        failure_mode=failure_mode,
        judged_by="rule",
        step_count=step_count,
        duration_s=duration_s,
        tokens=100,
        cost_cny=cost_cny,
        reused=reused,
        escalated=escalated,
    )


class TestComputeRunMetrics:
    def test_hit_rates_exclude_reused_and_escalated(self):
        """G7/D-64：复用出口与 escalated 不进命中分母，命中只看 eligible 行。"""
        rows = [
            _row(verdict="top1"),
            _row(verdict="top1"),
            _row(verdict="top3"),
            _row(verdict="miss"),
            _row(verdict="top1", reused=True),  # 复用出口：不计分母
            _row(verdict="miss", escalated=True),  # escalated：不计分母不计失败
        ]
        m = compute_run_metrics(rows)
        assert m["total"] == 6
        assert m["eligible"] == 4
        assert m["top1_hits"] == 2
        assert m["top3_hits"] == 3  # top3 命中含 top1
        assert m["top1_rate"] == 0.5
        assert m["top3_rate"] == 0.75
        assert m["reused_count"] == 1  # 单列（D-57）
        assert m["escalated_count"] == 1  # 单列不计失败（D-56）
        assert m["miss_count"] == 1  # escalated miss 未被计入

    def test_no_div_zero_empty_and_all_excluded(self):
        """空集 / 全复用 / 全 escalated：比率 None + defined=False（不除零先例同款）。"""
        empty = compute_run_metrics([])
        assert empty["rates_defined"] is False
        assert empty["top1_rate"] is None and empty["top3_rate"] is None

        all_reused = compute_run_metrics([_row(reused=True), _row(reused=True)])
        assert all_reused["rates_defined"] is False

        all_escalated = compute_run_metrics([_row(escalated=True), _row(escalated=True)])
        assert all_escalated["rates_defined"] is False

    def test_cost_steps_duration_mean_and_range(self):
        """G4：均值±极差（小样本用极差不用方差）。"""
        rows = [
            _row(step_count=2, duration_s=1.0, cost_cny=0.2),
            _row(step_count=4, duration_s=3.0, cost_cny=0.4),
            _row(step_count=10, duration_s=6.0, cost_cny=0.9),
            _row(step_count=99, duration_s=99.0, cost_cny=99.0, reused=True),  # 不进均值
        ]
        m = compute_run_metrics(rows)
        assert m["steps_avg"] == (2 + 4 + 10) / 3
        assert m["steps_range"] == 8
        assert m["duration_avg"] == (1.0 + 3.0 + 6.0) / 3
        assert m["duration_range"] == 5.0
        assert m["cost_avg"] == (0.2 + 0.4 + 0.9) / 3
        assert m["cost_range"] == 0.7  # G3：cost_cny 列，与 usage_log 实测列分列不混算


class TestFailureModeChain:
    def test_majority_wins(self):
        """G6：多数归类——同值过半即归该值。"""
        assert classify_failure_mode(["tool_error", "tool_error", "plan_error"]) == "tool_error"

    def test_no_majority_falls_to_unknown(self):
        """G4/G6：无多数落 unknown，禁丢弃。"""
        assert classify_failure_mode(["tool_error", "plan_error"]) == "unknown"

    def test_unknown_carries_human_channel_flag(self):
        """G6：unknown 强制人工通道有记录——requires_human 置位、计数不丢。"""
        rows = [_row(verdict="miss"), _row(verdict="miss")]
        m = compute_run_metrics(rows)
        assert m["failure_mode"] == "unknown"
        assert m["unknown_requires_human"] is True
        assert m["failure_modes"]["unknown"] == 2

    def test_hit_rows_not_in_failure_accounting(self):
        """命中行 failure_mode 为 null（eval_models 契约），不进失败归类。"""
        m = compute_run_metrics([_row(verdict="top1"), _row(verdict="top3")])
        assert m["failure_modes"] == {}
        assert m["failure_mode"] is None
        assert m["unknown_requires_human"] is False

    def test_escalated_excluded_from_failure_accounting(self):
        """G7：escalated 不计失败——即使带 failure_mode 也不进归类。"""
        m = compute_run_metrics([_row(verdict="miss", escalated=True, failure_mode="timeout")])
        assert m["failure_modes"] == {}
        assert m["escalated_count"] == 1


class TestSummarizeRuns:
    def test_group_by_scenario_model_with_unstable_flag(self):
        """D-61：同剧本 N 遍判定不一致 → unstable 单列，不静默平均。"""
        rows = [
            _row(scenario="cpu-spike", run_idx=0, verdict="top1"),
            _row(scenario="cpu-spike", run_idx=1, verdict="top1"),
            _row(scenario="cpu-spike", run_idx=2, verdict="miss"),
            _row(scenario="oom", run_idx=0, verdict="top1"),
            _row(scenario="oom", run_idx=1, verdict="top1"),
            _row(scenario="oom", run_idx=2, verdict="top1"),
        ]
        summary = {(g["scenario"], g["model"]): g for g in summarize_runs(rows)}
        assert summary[("cpu-spike", "mock")]["unstable"] is True
        assert summary[("oom", "mock")]["unstable"] is False
        assert summary[("cpu-spike", "mock")]["n_runs"] == 3

    def test_failure_mode_majority_per_group(self):
        """G4：失败模式按多数归类在组内进行。"""
        rows = [
            _row(scenario="a", verdict="miss", failure_mode="tool_error", run_idx=0),
            _row(scenario="a", verdict="miss", failure_mode="tool_error", run_idx=1),
            _row(scenario="a", verdict="miss", failure_mode="plan_error", run_idx=2),
        ]
        (group,) = summarize_runs(rows)
        assert group["failure_mode"] == "tool_error"
        assert group["unknown_requires_human"] is False

    def test_mean_and_range_within_group(self):
        """组内 N=3 均值±极差（D-61 口径）。"""
        rows = [
            _row(step_count=2, run_idx=0),
            _row(step_count=4, run_idx=1),
            _row(step_count=9, run_idx=2),
        ]
        (group,) = summarize_runs(rows)
        assert group["steps_avg"] == 5
        assert group["steps_range"] == 7
