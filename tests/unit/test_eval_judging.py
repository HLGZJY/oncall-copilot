"""M7-T3 验收 · 判对错管线（m7 issue 03 / G2 两级 + D-59 判定三值）。

- 规则匹配层：结论/假设与黄金 root_cause 做 `normalize_hypothesis_text`
  规范化包含判定（D-27② 复用勿重造），命中即 top1/top3，未命中 miss；
  可直接作为 issue 02 的 `Judger` 接缝实现传入 `run_scenario`。
- judge 契约：输入=结论+证据摘要+黄金标注，输出=`{verdict, reason}`；
  mock 冻结实现畸形输出抛 `LLMOutputError`（异常契约逐字对齐 D-07，
  真实 judge 是 key 门槛票，本票零真实调用）。
- 防自评：judge 模型走 `ONCALL_JUDGE_LLM_*` 独立 env 前缀，与被评模型
  `ONCALL_LLM_*` 解耦（解耦由常量契约机械保证）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

from oncall.classify.client import LLMClassifierError, LLMOutputError
from oncall.db import create_tables
from oncall.eval.golden import GoldenScenario
from oncall.eval.judging import (
    JUDGE_ENV_PREFIX,
    JUDGE_ENV_SUFFIXES,
    JUDGE_MODEL_ENV,
    Judge,
    JudgeInput,
    JudgeOutput,
    MockJudge,
    RealJudge,
    judger_from_judge,
    resolve_judge_config,
    rule_judge,
    two_tier_judger,
)
from oncall.eval.runner import Judgment, ScenarioCase, ScenarioRunSpec, run_scenario
from oncall.harness.loop import InvestigationResult, LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner, PlannerDecision
from oncall.harness.session import Hypothesis, SessionStatus
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.tools.schemas import ToolResult, ToolStatus
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict


def _now() -> datetime:
    return datetime.now(UTC)


def _golden() -> GoldenScenario:
    return GoldenScenario(
        scenario="cpu-spike",
        root_cause="cpu 饱和导致事件循环饥饿",
        remediation="扩容并限流",
    )


def _result(
    conclusion: str | None,
    hypotheses: list[str] | None = None,
) -> InvestigationResult:
    """构造最小 InvestigationResult（只填判定消费面字段）。"""
    return InvestigationResult(
        incident_id=1,
        status=SessionStatus.CONCLUDED,
        conclusion=conclusion,
        failure_mode=None,
        step_count=3,
        total_tokens=0,
        total_cost_cny=0.0,
        stop_reason="结论收束",
        hypotheses=[Hypothesis(text=text) for text in hypotheses or []],
    )


# ── 验收①：规则匹配层（top1 / 规范化包含 top3 / miss 边界）──


def test_rule_top1_conclusion_contains_root_cause():
    judgment = rule_judge(_golden(), _result("确认 cpu 饱和导致事件循环饥饿，建议扩容"))
    assert judgment.verdict == "top1"
    assert judgment.judged_by == "rule"
    assert judgment.reason


def test_rule_top1_normalization_ignores_punct_and_case():
    """规范化包含判定：标点/空白/大小写差异不误判（D-27② 复用）。"""
    conclusion = "CPU 饱和，导致事件循环饥饿！"
    judgment = rule_judge(_golden(), _result(conclusion))
    assert judgment.verdict == "top1"


def test_rule_top3_hypothesis_contains_root_cause():
    judgment = rule_judge(
        _golden(), _result("结论未命中：内存泄漏", ["慢 SQL 锁等待", "cpu 饱和导致事件循环饥饿"])
    )
    assert judgment.verdict == "top3"
    assert judgment.judged_by == "rule"


def test_rule_miss_when_nothing_matches():
    judgment = rule_judge(_golden(), _result("磁盘 IO 打满", ["网络抖动", "连接池耗尽"]))
    assert judgment.verdict == "miss"
    assert judgment.judged_by == "rule"


def test_rule_miss_on_empty_conclusion():
    judgment = rule_judge(_golden(), _result(None))
    assert judgment.verdict == "miss"


# ── 验收②：judge 契约（mock 冻结，畸形输出 → LLMOutputError 族 D-07）──


def test_judge_contract_input_output_shapes():
    payload = JudgeInput(
        conclusion="cpu 饱和",
        evidence_summary="指标 up 异常；日志见 saturate",
        golden_scenario="cpu-spike",
        golden_root_cause="cpu 饱和导致事件循环饥饿",
    )
    judge = MockJudge(verdict="top1", reason="结论与黄金根因语义一致")
    assert isinstance(judge, Judge)  # runtime_checkable 契约面
    out = judge.judge(payload)
    assert isinstance(out, JudgeOutput)
    assert out.verdict == "top1" and out.reason


def test_mock_judge_replays_script_then_default():
    payload = JudgeInput(
        conclusion="x", evidence_summary="", golden_scenario="s", golden_root_cause="r"
    )
    judge = MockJudge(
        script=[
            JudgeOutput.model_validate({"verdict": "top3", "reason": "假设层命中"}),
            LLMOutputError("judge 返回空内容，无法解析为契约 JSON"),
        ],
        verdict="miss",
        reason="mock 默认",
    )
    assert judge.judge(payload).verdict == "top3"
    with pytest.raises(LLMOutputError):
        judge.judge(payload)
    assert judge.judge(payload).verdict == "miss"  # 耗尽后稳定默认（不抛 StopIteration）


def test_mock_judge_malformed_output_raises_llm_output_error():
    """畸形输出（枚举越界/缺字段）→ LLMOutputError，对齐 infra/llm.py D-07 文案。"""
    payload = JudgeInput(
        conclusion="x", evidence_summary="", golden_scenario="s", golden_root_cause="r"
    )
    judge = MockJudge(script=[{"verdict": "excellent", "reason": "枚举越界"}])
    with pytest.raises(LLMOutputError, match="契约校验"):
        judge.judge(payload)


def test_mock_judge_records_calls():
    payload = JudgeInput(
        conclusion="c", evidence_summary="e", golden_scenario="s", golden_root_cause="r"
    )
    judge = MockJudge()
    judge.judge(payload)
    assert judge.calls == [payload]


# ── 防自评：judge env 前缀与被评模型解耦 ──


def test_judge_env_prefix_decoupled_from_evaluated_model():
    assert JUDGE_ENV_PREFIX == "ONCALL_JUDGE_LLM"
    assert not JUDGE_MODEL_ENV.startswith("ONCALL_LLM_")
    assert JUDGE_MODEL_ENV == f"{JUDGE_ENV_PREFIX}_MODEL"


# ── 验收③：与 issue 02 接缝连通 ──


def test_judger_adapter_converts_to_judgment():
    judge = MockJudge(verdict="top1", reason="契约产出")
    judger = judger_from_judge(judge)
    judgment = judger(_golden(), _result("任意结论"))
    assert isinstance(judgment, Judgment)
    assert judgment.verdict == "top1"
    assert judgment.judged_by == "judge"
    assert judgment.reason == "契约产出"


def test_judger_adapter_passes_contract_input():
    judge = MockJudge()
    judger = judger_from_judge(judge)
    judger(_golden(), _result("结论", ["假设甲"]))
    payload = judge.calls[0]
    assert payload.conclusion == "结论"
    assert "假设甲" in payload.evidence_summary
    assert payload.golden_scenario == "cpu-spike"
    assert payload.golden_root_cause == "cpu 饱和导致事件循环饥饿"


def test_rule_judge_usable_as_judger_in_run_scenario():
    """规则匹配层直接作为 `Judger` 传入 run_scenario（issue 02 接缝连通）。"""

    def factory(golden: GoldenScenario, run_idx: int) -> LoopComponents:
        anchor = datetime(2026, 9, 6, 6, 28, 21, tzinfo=UTC).isoformat()
        script = [
            PlannerDecision.model_validate(
                {
                    "thought": "查询核心指标",
                    "next_tool": "query_metrics",
                    "args": {"promql": "up", "start": anchor, "end": anchor},
                }
            ),
            PlannerDecision.model_validate(
                {"thought": "证据已足够", "conclusion": golden.root_cause}
            ),
        ]

        def metrics_handler(args: object, *, timeout_seconds: float) -> ToolResult:
            return ToolResult(
                tool="query_metrics", status=ToolStatus.OK, data={"direction": "up"}, meta={}
            )

        def unused_stub(args: object, *, timeout_seconds: float) -> ToolResult:
            return ToolResult(tool="stub", status=ToolStatus.OK, data={}, meta={})

        registry = ToolRegistry(now=_now)
        register_six_tools(
            registry,
            {
                "query_metrics": metrics_handler,
                "search_logs": unused_stub,
                "detect_anomaly": unused_stub,
                "get_topology": unused_stub,
            },
        )
        verdict = VerifierVerdict(supported=True, reason="mock 裁决")
        return LoopComponents(
            planner=MockPlanner(script=script),
            registry=registry,
            gate=PermissionGate(now=_now),
            verifier=Verifier(judge=MockVerifierJudge(default=verdict)),
            now=_now,
        )

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    with Session(engine, expire_on_commit=False) as session:
        rows = run_scenario(
            case=ScenarioCase(
                golden=_golden(),
                spec=ScenarioRunSpec(scenario="cpu-spike", the_set="dev", model="mock-profile"),
            ),
            components_factory=factory,
            judger=rule_judge,
            db_session=session,
        )
        session.commit()
    assert all(row.verdict == "top1" for row in rows)
    assert all(row.judged_by == "rule" for row in rows)


# ── M7-T7：真实 judge 装配（ONCALL_JUDGE_LLM_* env → infra client 注入）──


class _FakeJudgeClient:
    """OpenAIJudgeClient 替身（契约面：chat_json + usage_log，零真实调用）。"""

    def __init__(self, payloads: list[dict[str, str] | Exception]) -> None:
        self._payloads = list(payloads)
        self._cursor = 0
        self.prompts: list[tuple[str, str]] = []
        self.usage_log: list[object] = []

    def chat_json(self, system: str, user: str) -> dict[str, str]:
        self.prompts.append((system, user))
        item = self._payloads[self._cursor]
        self._cursor += 1
        if isinstance(item, Exception):
            raise item
        return item


def test_resolve_judge_config_maps_prefix_to_llm_env():
    env = {
        "ONCALL_JUDGE_LLM_BASE_URL": "https://api.moonshot.cn/v1",
        "ONCALL_JUDGE_LLM_MODEL": "moonshot-v1-8k",
        "ONCALL_JUDGE_LLM_API_KEY": "sk-test",
        "ONCALL_JUDGE_LLM_TIMEOUT_SECONDS": "20",
    }
    config = resolve_judge_config(env)
    assert config.base_url == "https://api.moonshot.cn/v1"
    assert config.model == "moonshot-v1-8k"
    assert config.api_key == "sk-test"
    assert config.timeout_seconds == 20.0


def test_resolve_judge_config_suffixes_frozen():
    """judge env 尾词与 profile 同词汇（BASE_URL/MODEL/API_KEY/TIMEOUT_SECONDS）。"""
    assert JUDGE_ENV_SUFFIXES == ("BASE_URL", "MODEL", "API_KEY", "TIMEOUT_SECONDS")


def test_real_judge_wraps_client_and_validates_contract():
    client = _FakeJudgeClient([{"verdict": "top3", "reason": "排查方向命中"}])
    judge = RealJudge(client)
    assert isinstance(judge, Judge)
    out = judge.judge(
        JudgeInput(
            conclusion="内存泄漏",
            evidence_summary="假设甲; 假设乙",
            golden_scenario="cpu-spike",
            golden_root_cause="cpu 饱和",
        )
    )
    assert out.verdict == "top3"
    system, user = client.prompts[0]
    assert "JSON" in system and "cpu 饱和" in user and "假设甲" in user


def test_real_judge_malformed_output_raises_llm_output_error():
    client = _FakeJudgeClient([{"verdict": "excellent", "reason": "越界"}])
    judge = RealJudge(client)
    payload = JudgeInput(
        conclusion="c", evidence_summary="e", golden_scenario="s", golden_root_cause="r"
    )
    with pytest.raises(LLMOutputError, match="契约校验"):
        judge.judge(payload)


def test_two_tier_rule_hit_short_circuits_without_judge_call():
    client = _FakeJudgeClient([])
    judger = two_tier_judger(RealJudge(client))
    judgment = judger(_golden(), _result("确认 cpu 饱和导致事件循环饥饿"))
    assert judgment.verdict == "top1" and judgment.judged_by == "rule"
    assert client.prompts == []  # 规则命中零 judge 成本


def test_two_tier_miss_falls_back_to_judge():
    client = _FakeJudgeClient([{"verdict": "top3", "reason": "假设层语义命中"}])
    judger = two_tier_judger(RealJudge(client))
    judgment = judger(_golden(), _result("磁盘 IO 打满", ["网络抖动"]))
    assert judgment.verdict == "top3"
    assert judgment.judged_by == "judge"
    assert len(client.prompts) == 1


def test_two_tier_judge_crash_falls_back_to_rule_miss_with_trace():
    """judge 异常不炸跑批：重试 1 次后回退规则 miss，reason 留 judge_error 追溯痕。"""
    client = _FakeJudgeClient([LLMClassifierError("限流"), LLMClassifierError("仍失败")])
    judger = two_tier_judger(RealJudge(client))
    judgment = judger(_golden(), _result("磁盘 IO 打满"))
    assert judgment.verdict == "miss"
    assert judgment.judged_by == "rule"
    assert "judge_error" in judgment.reason
    assert len(client.prompts) == 2  # 重试 ≤1 次
