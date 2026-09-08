"""M3-04 ContextManager 测试（D-25 / 架构 §3.3 定值）。

四块：摘要模板纯函数（四要素 + 确定性）/ 工具输出 ≤2000 tokens 截断 + 指针 /
系统提示 ≤1500 tokens（六工具一览 + 输出协议）/ 步数 ≥10 移出已证伪假设
（视图层过滤：窗口不含 rejected、session 全量保留、active/confirmed 不动）。
token 估算函数可注入（票面 G8：默认 2 字符 ≈ 1 token；测试用恒等/放大替身钉边界，
踩坑⑪：勿硬编码假设字符分布）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from oncall.harness.context_manager import (
    DEFAULT_EVICTION_THRESHOLD,
    SYSTEM_PROMPT_TOKEN_LIMIT,
    TOKEN_BUDGET,
    build_system_prompt,
    estimate_tokens,
    summarize_step,
    truncate_to_budget,
    visible_hypotheses,
)
from oncall.harness.session import (
    EvidenceStep,
    Hypothesis,
    HypothesisStatus,
    InvestigationSession,
)
from oncall.harness.tools.registry import TOOL_SPECS

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

_TS = datetime(2026, 9, 8, 8, 0, 0, tzinfo=UTC)


def make_step(step_no: int = 1, **overrides: object) -> EvidenceStep:
    payload: dict[str, object] = {
        "step_no": step_no,
        "thought": "队列堆积，先查消费者延迟",
        "tool": "query_metrics",
        "input_json": {
            "component": "order-service",
            "query": "queue_lag",
            "start": "2026-09-08T07:30:00+00:00",
            "end": "2026-09-08T08:00:00+00:00",
        },
        "output_json": {"direction": "up", "values": [1.0, 2.0]},
        "output_summary": "queue_lag 持续上升",
        "tokens": 120,
        "cost_cny": 0.0,
        "latency_ms": 300,
        "ts": _TS,
    }
    payload.update(overrides)
    return EvidenceStep.model_validate(payload)


def make_session(
    step_count: int, hypotheses: list[Hypothesis] | None = None
) -> InvestigationSession:
    """只造假设集合与步计数的轻量 session（steps 用 record_step 补齐步号连续）。"""
    session = InvestigationSession(incident_id=1)
    for no in range(1, step_count + 1):
        session.record_step(make_step(step_no=no))
    for hyp in hypotheses or []:
        session.add_hypothesis(hyp)
    return session


def make_hypothesis(text: str, status: HypothesisStatus) -> Hypothesis:
    return Hypothesis(text=text, status=status)


def identity_estimator(text: str) -> int:
    """恒等替身（踩坑⑪）：1 字符 = 1 token。"""
    return len(text)


def amplify_estimator(text: str) -> int:
    """放大替身（踩坑⑪）：1 字符 = 10 tokens，验证估算口径替换后预算语义不变。"""
    return len(text) * 10


# ---------------------------------------------------------------------------
# 摘要模板纯函数（架构 §3.3 四要素）
# ---------------------------------------------------------------------------


class TestSummarizeStep:
    def test_four_elements_present(self) -> None:
        """四要素齐全：组件/指标/异常方向/时间窗。"""
        summary = summarize_step(make_step())
        assert "组件=order-service" in summary
        assert "指标=queue_lag" in summary
        assert "异常方向=up" in summary
        assert "时间窗=" in summary
        assert "2026-09-08T07:30:00" in summary and "2026-09-08T08:00:00" in summary

    def test_deterministic_byte_identical(self) -> None:
        """同输入两次调用输出逐字节相同（压缩 KV cache 重复前缀的前提）。"""
        step = make_step()
        assert summarize_step(step) == summarize_step(step)

    def test_missing_elements_use_na_placeholder(self) -> None:
        """变量缺失时确定性占位 n/a，不留 f-string {} 残片（踩坑⑩）。"""
        step = make_step(input_json={}, output_json={})
        summary = summarize_step(step)
        assert summary.count("n/a") == 4
        assert "{}" not in summary

    def test_non_tool_step_fallback(self) -> None:
        """thought 步（无工具语义）同样四要素齐全不抛错。"""
        step = make_step(tool="think", input_json={}, output_json={})
        assert summarize_step(step).count("n/a") == 4


# ---------------------------------------------------------------------------
# 工具输出预算（≤2000 tokens 截断 + 指针，G4）
# ---------------------------------------------------------------------------


class TestTruncateToBudget:
    def test_exactly_at_budget_not_truncated(self) -> None:
        """恰等于预算不截断（恒等替身：2000 字符 = 2000 tokens）。"""
        summary = "x" * TOKEN_BUDGET
        out, tokens, truncated = truncate_to_budget(
            summary, step_no=3, estimator=identity_estimator
        )
        assert out == summary
        assert tokens == TOKEN_BUDGET
        assert truncated is False

    def test_over_budget_truncated_with_pointer(self) -> None:
        """超预算截断 + `[truncated, full at step N]` 指针。"""
        summary = "x" * (TOKEN_BUDGET + 1)
        out, tokens, truncated = truncate_to_budget(
            summary, step_no=3, estimator=identity_estimator
        )
        assert truncated is True
        assert tokens <= TOKEN_BUDGET
        assert "[truncated, full at step 3]" in out

    def test_amplified_estimator_triggers(self) -> None:
        """放大替身验证：估算口径替换后预算语义不变（踩坑⑪）。"""
        out, tokens, truncated = truncate_to_budget(
            "y" * 300, step_no=1, estimator=amplify_estimator
        )
        assert truncated is True
        assert tokens <= TOKEN_BUDGET
        assert "[truncated, full at step 1]" in out

    def test_default_estimator_g8_boundary(self) -> None:
        """默认口径（G8：2 字符 ≈ 1 token）边界：恰 2×budget 字符不截，+1 截。"""
        at_limit = "z" * (TOKEN_BUDGET * 2)
        _, tokens, truncated = truncate_to_budget(at_limit, step_no=1)
        assert truncated is False and tokens == TOKEN_BUDGET
        _, _, truncated_over = truncate_to_budget(f"{at_limit}!", step_no=1)
        assert truncated_over is True

    def test_pointer_traceable_to_evidence_step(self) -> None:
        """指针语义：截断只动摘要，EvidenceStep.output_json 原始输出完整可回溯。"""
        big_output = {"values": list(range(5000))}
        step = make_step(step_no=7, output_json=big_output)
        out, _, truncated = truncate_to_budget(
            step.output_summary + "x" * (TOKEN_BUDGET * 2), step_no=step.step_no
        )
        assert truncated and "[truncated, full at step 7]" in out
        assert step.output_json == big_output  # 原始输出未动

    def test_default_estimator_matches_g8(self) -> None:
        """默认估算与 M2 同款粗估口径一致（2 字符 ≈ 1 token）。"""
        assert estimate_tokens("abcd") == 2
        assert estimate_tokens("abc") == 2


# ---------------------------------------------------------------------------
# 系统提示预算（≤1500 tokens，D-22/D-23）
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_within_1500_tokens(self) -> None:
        """六工具一览 + 输出协议组装后整体 ≤1500 tokens（G8 默认口径）。"""
        prompt = build_system_prompt()
        assert estimate_tokens(prompt) <= SYSTEM_PROMPT_TOKEN_LIMIT

    def test_schema_summary_within_1500_after_d38(self) -> None:
        """D-38：附六工具入参 schema 摘要后整体仍 ≤1500 tokens（预算断言随票钉死）。"""
        prompt = build_system_prompt()
        assert estimate_tokens(prompt) <= SYSTEM_PROMPT_TOKEN_LIMIT
        assert "## 工具入参 schema" in prompt

    def test_schema_aligns_with_registry_contracts(self) -> None:
        """schema 摘要与 registry 注册契约逐字对齐（本票核心质量线，漂移即红）。

        归属断言从 `spec.schema.model_fields`（运行时校验的同一数据源）独立
        推导，不复用生产渲染函数——必填/可选任一漂移测试即红。
        """
        prompt = build_system_prompt()
        lines = prompt.split("\n")
        for spec in TOOL_SPECS:
            fields = spec.schema.model_fields
            required = [n for n, f in fields.items() if f.is_required()]
            optional = [n for n, f in fields.items() if not f.is_required()]
            head_idx = next(
                i for i, line in enumerate(lines) if line.startswith(f"- {spec.name}：必填 ")
            )
            head_line = lines[head_idx]
            for name in required:
                assert name in head_line, f"{spec.name} 必填参数 {name} 缺失"
            if optional:
                opt_line = lines[head_idx + 1]
                assert opt_line.startswith("  可选 ")
                for name in optional:
                    assert f"{name}?" in opt_line, f"{spec.name} 可选参数 {name} 未带 ? 标记"
                    assert name not in head_line, f"{spec.name} 可选参数 {name} 误入必填段"

    def test_schema_pins_key_constraints(self) -> None:
        """关键约束值域钉进 prompt：query_metrics 必填 start/end、search_logs limit≤100。"""
        prompt = build_system_prompt()
        assert "start、end" in prompt  # query_metrics 必填时间窗（M3 issue 08 注记②主修复）
        assert "limit?(≥1,≤100)" in prompt
        assert "direction?(backward|forward)" in prompt
        assert "top_k?(≥1,≤10)" in prompt

    def test_schema_optional_marked_and_described(self) -> None:
        """可选参数带 ? 标记；schema 内 description 随契约透出（派生非手抄）。"""
        prompt = build_system_prompt()
        assert "step?" in prompt
        assert "缺省由实现侧定" in prompt

    def test_lists_all_six_tools_from_specs(self) -> None:
        """工具一览从 TOOL_SPECS 生成——六工具名与一句话描述全在（勿手抄第二份）。"""
        prompt = build_system_prompt()
        for spec in TOOL_SPECS:
            assert spec.name in prompt
            assert spec.description in prompt

    def test_output_protocol_d22(self) -> None:
        """输出协议含 D-22 两分支互斥语义：{thought, next_tool, args} | {conclusion}。"""
        prompt = build_system_prompt()
        for marker in ("thought", "next_tool", "args", "conclusion"):
            assert marker in prompt

    def test_deterministic(self) -> None:
        assert build_system_prompt() == build_system_prompt()

    def test_over_budget_raises_with_amplified_estimator(self) -> None:
        """超预算直接抛错（模板膨胀事故，勿放宽预算）。"""
        with pytest.raises(ValueError, match="超预算上限"):
            build_system_prompt(estimator=amplify_estimator)


# ---------------------------------------------------------------------------
# 步数 ≥10 移出已证伪假设（架构 §3.3 / D-25 / D-27③）
# ---------------------------------------------------------------------------


class TestEvictRejectedHypotheses:
    def _hypotheses(self) -> list[Hypothesis]:
        return [
            make_hypothesis("活跃假设：消费组 rebalance", HypothesisStatus.ACTIVE),
            make_hypothesis("已证伪：磁盘打满", HypothesisStatus.REJECTED),
            make_hypothesis("已证实：队列堆积", HypothesisStatus.CONFIRMED),
        ]

    def test_below_threshold_all_visible(self) -> None:
        """步数 <10：主上下文窗口全量可见。"""
        session = make_session(9, self._hypotheses())
        assert visible_hypotheses(session) == session.hypotheses

    def test_at_threshold_rejected_hidden_session_retained(self) -> None:
        """步数 ≥10：窗口不含 rejected；session.hypotheses 仍全量保留（落库保留）。"""
        session = make_session(DEFAULT_EVICTION_THRESHOLD, self._hypotheses())
        visible = visible_hypotheses(session)
        assert all(h.status is not HypothesisStatus.REJECTED for h in visible)
        assert len(session.hypotheses) == 3  # session 全量保留
        assert any(h.status is HypothesisStatus.REJECTED for h in session.hypotheses)

    def test_active_and_confirmed_untouched(self) -> None:
        """active/confirmed 不动：两态仍在窗口内且原文不变。"""
        session = make_session(DEFAULT_EVICTION_THRESHOLD, self._hypotheses())
        visible = visible_hypotheses(session)
        texts = {h.text for h in visible}
        assert "活跃假设：消费组 rebalance" in texts
        assert "已证实：队列堆积" in texts

    def test_threshold_injectable(self) -> None:
        """步数阈值与步数读取可注入（G8 判据 1：决策输入可注入，T6 复用）。"""
        session = make_session(3, self._hypotheses())
        assert visible_hypotheses(session, threshold=3) != session.hypotheses
        assert visible_hypotheses(session, step_count=99) != session.hypotheses

    @pytest.mark.parametrize("count", [10, 15])
    def test_boundary_ge_threshold(self, count: int) -> None:
        """边界：恰等于 10 即移出（架构定值「步数 ≥ 10」）。"""
        session = make_session(count, self._hypotheses())
        visible = visible_hypotheses(session)
        assert all(h.status is not HypothesisStatus.REJECTED for h in visible)
