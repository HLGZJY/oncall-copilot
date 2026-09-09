"""M7 issue 05 验收 · N 遍矩阵 + 模型 profile 参数化 + 报告双产物。

G4：mock 档多 profile × 多剧本 × N=3 矩阵跑法，消费 issue 04 `summarize_runs`
（unstable 单列不静默平均）；G8：profile = `ONCALL_LLM_*` env 前缀参数化，
**零硬编码模型名**（本文件含机械检查测试）；G5：JSON 明细 + 汇总 markdown
双产物，每个数字可回溯 eval_runs 表行（硬规 10 禁虚构）。全程零真实 API 调用。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.db.eval_models import EvalRun
from oncall.eval.golden import GoldenScenario
from oncall.eval.report import (
    ModelProfile,
    build_matrix_report,
    render_markdown,
    resolve_profile,
    run_matrix,
    write_report,
)
from oncall.eval.runner import Judgment, ScenarioCase, ScenarioRunSpec
from oncall.harness.loop import LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner
from oncall.harness.tools.registry import ToolRegistry
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict
from oncall.infra.llm import LLMClientConfig, LLMConfigError

STAMP = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _golden(slug: str) -> GoldenScenario:
    return GoldenScenario(scenario=slug, root_cause=f"{slug} 根因", remediation="按 SOP 处置")


def _case(slug: str, model: str) -> ScenarioCase:
    return ScenarioCase(
        golden=_golden(slug),
        spec=ScenarioRunSpec(scenario=slug, the_set="dev", model=model),
    )


def _mock_factory(golden: GoldenScenario, run_idx: int, profile: ModelProfile) -> LoopComponents:
    """mock 档装配：MockPlanner 直接收束（确定性，零真实调用，零工具步）。"""
    return LoopComponents(
        planner=MockPlanner(conclusion=golden.root_cause),
        registry=ToolRegistry(now=_now),
        gate=PermissionGate(now=_now),
        verifier=Verifier(
            judge=MockVerifierJudge(default=VerifierVerdict(supported=True, reason="mock 裁决"))
        ),
        now=_now,
    )


def _matrix_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return Session(engine)


def _run_matrix_2x2x3() -> list[EvalRun]:
    """2 profile × 2 剧本 × 3 遍（mock 档矩阵形状与票面 2×12×3 同构）。"""
    profiles = [ModelProfile(name="profile-a"), ModelProfile(name="profile-b")]
    cases = [_case("cpu-spike", p.name) for p in profiles for _ in (0,)] + [
        _case("slow-sql", p.name) for p in profiles
    ]
    with _matrix_session() as session:
        return run_matrix(
            cases=cases,
            profiles=profiles,
            components_factory=_mock_factory,
            judger=lambda golden, result: Judgment(verdict="top1", judged_by="rule"),
            db_session=session,
        )


class TestRunMatrix:
    def test_matrix_shape_2_profiles_x_cases_x_3_runs(self) -> None:
        rows = _run_matrix_2x2x3()
        assert len(rows) == 2 * 2 * 3
        models = {row.model for row in rows}
        assert models == {"profile-a", "profile-b"}
        scenarios = {row.scenario for row in rows}
        assert scenarios == {"cpu-spike", "slow-sql"}
        for model in models:
            for scenario in scenarios:
                idx = {
                    row.run_idx for row in rows if row.model == model and row.scenario == scenario
                }
                assert idx == {0, 1, 2}

    def test_matrix_rows_are_persisted(self) -> None:
        rows = _run_matrix_2x2x3()
        assert all(row.id is not None for row in rows)


class TestProfileEnv:
    """G8：profile = env 前缀参数化，装配复用 `LLMClientConfig.from_env`。"""

    ENV: ClassVar[dict[str, str]] = {
        "ONCALL_LLM_PROFILE_DEEPSEEK_BASE_URL": "https://api.example.com/v1",
        "ONCALL_LLM_PROFILE_DEEPSEEK_MODEL": "  model-x  ",
        "ONCALL_LLM_PROFILE_DEEPSEEK_API_KEY": "sk-test",
        "ONCALL_LLM_PROFILE_DEEPSEEK_TIMEOUT_SECONDS": "9.5",
    }

    def test_resolve_profile_reads_prefixed_env(self) -> None:
        profile = ModelProfile(name="deepseek")
        config = resolve_profile(profile, env=self.ENV)
        assert isinstance(config, LLMClientConfig)
        assert config.base_url == "https://api.example.com/v1"
        assert config.model == "model-x"  # strip 与 from_env 同口径
        assert config.api_key == "sk-test"
        assert config.timeout_seconds == 9.5

    def test_resolve_profile_missing_fails_fast(self) -> None:
        profile = ModelProfile(name="qwen")
        with pytest.raises(LLMConfigError):
            resolve_profile(profile, env={})

    def test_suffixes_align_with_llm_switch_surface(self) -> None:
        """前缀 + 尾词拼出的 env 名必须落在既有 `ONCALL_LLM_*` 切换面词汇上。"""
        from oncall.infra.llm import API_KEY_ENV, BASE_URL_ENV, MODEL_ENV  # noqa: PLC0415

        profile = ModelProfile(name="x")
        for tail, full in (
            ("BASE_URL", BASE_URL_ENV),
            ("MODEL", MODEL_ENV),
            ("API_KEY", API_KEY_ENV),
        ):
            var = profile.env_prefix + tail
            assert var.startswith("ONCALL_LLM_PROFILE_")
            assert "ONCALL_LLM_" + tail == full  # 尾词与切换面同词汇（G8）

    def test_zero_hardcoded_model_names(self) -> None:
        """机械检查：实现文件不得出现任何具体模型名（G8 禁硬编码）。"""
        source = Path("src/oncall/eval/report.py").read_text(encoding="utf-8").lower()
        for token in ("deepseek", "qwen", "gpt", "claude", "kimi", "moonshot", "glm"):
            assert token not in source, f"report.py 硬编码了模型名: {token}"


def _known_rows(session: Session) -> list[EvalRun]:
    """构造已知行集合：数字全部预知，报告字段必须逐一可对上（硬规 10）。"""
    facts = [
        # (scenario, model, run_idx, verdict, step_count, cost_cny, unstable_group)
        ("cpu-spike", "profile-a", 0, "top1", 3, 0.10),
        ("cpu-spike", "profile-a", 1, "top1", 4, 0.12),
        ("cpu-spike", "profile-a", 2, "top3", 5, 0.14),  # 判定不一致 → unstable
        ("cpu-spike", "profile-b", 0, "top1", 2, 0.30),
        ("cpu-spike", "profile-b", 1, "top1", 2, 0.30),
        ("cpu-spike", "profile-b", 2, "top1", 2, 0.30),
        ("slow-sql", "profile-a", 0, "miss", 6, 0.20),
        ("slow-sql", "profile-a", 1, "miss", 6, 0.20),
        ("slow-sql", "profile-a", 2, "miss", 6, 0.20),
    ]
    rows = []
    for scenario, model, run_idx, verdict, steps, cost in facts:
        rows.append(
            EvalRun(
                scenario=scenario,
                the_set="dev",
                model=model,
                run_idx=run_idx,
                verdict=verdict,
                failure_mode="no_signal" if verdict == "miss" else None,
                judged_by="rule",
                step_count=steps,
                duration_s=1.0 * steps,
                tokens=100 * steps,
                cost_cny=cost,
                escalated=(scenario == "cpu-spike" and model == "profile-b" and run_idx == 2),
            )
        )
        session.add(rows[-1])
    session.flush()
    return rows


def _report_session() -> tuple[Session, list[EvalRun]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    session = Session(engine)
    return session, _known_rows(session)


class TestBuildMatrixReport:
    def test_numbers_traceable_to_eval_run_rows(self) -> None:
        _session, rows = _report_session()
        report = build_matrix_report(rows, generated_at=STAMP)
        groups = {(g["scenario"], g["model"]): g for g in report["groups"]}

        # profile-a × cpu-spike：2 top1 + 1 top3 / 3 eligible → 2/3、3/3；unstable 单列
        g = groups[("cpu-spike", "profile-a")]
        assert g["n_runs"] == 3
        assert g["unstable"] is True
        assert g["top1_hits"] == 2 and g["top3_hits"] == 3
        assert g["top1_rate"] == pytest.approx(2 / 3)
        assert g["top3_rate"] == pytest.approx(1.0)
        assert g["steps_avg"] == pytest.approx(4.0)
        assert g["steps_range"] == pytest.approx(2.0)
        assert g["cost_avg"] == pytest.approx((0.10 + 0.12 + 0.14) / 3)

        # profile-b × cpu-spike：1 行 escalated 单列不计失败，命中分母 2
        g = groups[("cpu-spike", "profile-b")]
        assert g["escalated_count"] == 1
        assert g["eligible"] == 2
        assert g["top1_rate"] == pytest.approx(1.0)
        assert g["unstable"] is False

        # profile-a × slow-sql：全 miss，失败模式 no_signal
        g = groups[("slow-sql", "profile-a")]
        assert g["top1_rate"] == 0.0
        assert g["failure_mode"] == "no_signal"

    def test_group_carries_run_ids_for_audit(self) -> None:
        """可回溯口径：每组携带 eval_runs 行 id，SQL 抽验可达。"""
        _session, rows = _report_session()
        report = build_matrix_report(rows, generated_at=STAMP)
        by_key = {(r.scenario, r.model, r.run_idx): r for r in rows}
        for g in report["groups"]:
            expected_ids = sorted(
                by_key[(g["scenario"], g["model"], i)].id for i in range(g["n_runs"])
            )
            assert g["run_ids"] == expected_ids
            assert all(isinstance(i, int) for i in g["run_ids"])

    def test_report_carries_provenance(self) -> None:
        _session, rows = _report_session()
        report = build_matrix_report(rows, generated_at=STAMP)
        assert report["generated_at"] == STAMP.isoformat()
        assert report["source"] == "eval_runs"
        assert set(report["profiles"]) == {"profile-a", "profile-b"}


class TestRenderMarkdown:
    def test_matrix_table_contains_key_columns(self) -> None:
        _session, rows = _report_session()
        md = render_markdown(build_matrix_report(rows, generated_at=STAMP))
        for header in ("剧本", "模型", "Top-1", "Top-3", "步数", "成本", "unstable", "失败模式"):
            assert header in md
        assert "profile-a" in md and "slow-sql" in md

    def test_selection_skeleton_present(self) -> None:
        """G5：选型理由骨架（真实档实测后回填），缺省不虚构结论。"""
        _session, rows = _report_session()
        md = render_markdown(build_matrix_report(rows, generated_at=STAMP))
        assert "选型理由" in md
        assert "待真实档实测回填" in md

    def test_traceability_note_present(self) -> None:
        _session, rows = _report_session()
        md = render_markdown(build_matrix_report(rows, generated_at=STAMP))
        assert "eval_runs" in md


class TestWriteReport:
    def test_writes_json_and_markdown(self, tmp_path: Path) -> None:
        _session, rows = _report_session()
        json_path, md_path = write_report(rows, tmp_path, generated_at=STAMP)
        assert json_path.name == "runs-20260909.json"
        assert md_path.name == "report-20260909.md"
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["source"] == "eval_runs"
        assert len(payload["groups"]) == 3
        assert "选型理由" in md_path.read_text(encoding="utf-8")
