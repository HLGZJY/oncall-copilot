"""M7 issue 06 验收 · make eval / make eval-real 一键入口（G1 回归面）。

- mock 档一键：2 profile × 12 dev 剧本 × N=3 矩阵，组件装配全 mock
  （MockPlanner/MockVerifierJudge，零真实 API 调用），报告双产物落 out_dir；
- 真实档门槛：`ONCALL_RUN_M7_EVAL=1` 未解锁 raise；解锁后入口可达、
  **不真跑**（真实档跑批与档位回填归 issue 07，本票只收口入口）。

DB 落 tmp sqlite（表是明细权威 D-62，入口不污染仓库 oncall.db）。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.eval.entry import (
    ENV_FILE_ENV,
    MOCK_PROFILE_NAMES,
    REAL_PROFILE_NAMES,
    load_env_file,
    main,
    run_eval_real,
    run_mock_eval,
)
from oncall.eval.judging import MockJudge
from oncall.eval.runner import REAL_TIER_ENV
from oncall.harness.loop import LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner
from oncall.harness.tools.registry import ToolRegistry
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict
from oncall.infra.llm import LLMConfigError


def _memory_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return Session(engine)


class TestMockEval:
    def test_matrix_shape_2_profiles_x_12_dev_x_3(self) -> None:
        """票面口径：2 profile × 12 dev 剧本 × 3 遍 = 72 行，全部落 dev 集。"""
        with _memory_session() as session:
            rows, _, _ = run_mock_eval(db_session=session)
        assert len(rows) == 2 * 12 * 3
        assert {row.model for row in rows} == set(MOCK_PROFILE_NAMES)
        assert {row.the_set for row in rows} == {"dev"}
        assert len({row.scenario for row in rows}) == 12
        for model in MOCK_PROFILE_NAMES:
            per_model = [row for row in rows if row.model == model]
            assert len(per_model) == 12 * 3
            assert len({row.scenario for row in per_model}) == 12

    def test_report_dual_artifacts_written(self, tmp_path: Path) -> None:
        """G5：JSON + markdown 双产物落 out_dir，JSON groups 与矩阵格数一致。"""
        with _memory_session() as session:
            _, json_path, md_path = run_mock_eval(db_session=session, out_dir=tmp_path)
        assert json_path.parent == tmp_path
        assert json_path.name.startswith("runs-") and json_path.suffix == ".json"
        assert md_path.name.startswith("report-") and md_path.suffix == ".md"
        report = json.loads(json_path.read_text(encoding="utf-8"))
        assert report["source"] == "eval_runs"
        assert sorted(report["profiles"]) == sorted(MOCK_PROFILE_NAMES)
        assert len(report["groups"]) == 2 * 12  # 每格 = 剧本 × profile 聚合一行
        assert "Top-1" in md_path.read_text(encoding="utf-8")

    def test_default_judger_is_rule_layer(self) -> None:
        """入口判定接缝 = issue 03 规则匹配层（judged_by 恒 rule）。"""
        with _memory_session() as session:
            rows, _, _ = run_mock_eval(db_session=session)
        assert {row.judged_by for row in rows} == {"rule"}
        assert {row.verdict for row in rows} <= {"top1", "top3", "miss"}


class TestRealTierGate:
    def test_locked_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(REAL_TIER_ENV, raising=False)
        with pytest.raises(RuntimeError, match=REAL_TIER_ENV):
            run_eval_real()

    def test_unlocked_env_0_still_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(REAL_TIER_ENV, "0")
        with pytest.raises(RuntimeError, match=REAL_TIER_ENV):
            run_eval_real()


# ── M7-T7：真实档兑现（run_eval_real 实跑装配面）──


def _write_tiny_golden(root: Path) -> Path:
    dev = root / "dev"
    dev.mkdir(parents=True)
    (dev / "cpu-spike.yaml").write_text(
        "scenario: cpu-spike\n"
        "root_cause: stress 进程把 cpu 打满\n"
        "remediation: 停探针\n"
        "investigation_path: [查指标]\n"
        "runs: []\n",
        encoding="utf-8",
    )
    return root


def _fake_factory(golden: object, run_idx: int, profile: object) -> object:
    """注入替身：MockPlanner 结论不中 golden 根因 → 走 judge 回退分支。"""
    del run_idx, profile
    return LoopComponents(
        planner=MockPlanner(conclusion="磁盘 IO 打满"),
        registry=ToolRegistry(),
        gate=PermissionGate(),
        verifier=Verifier(
            judge=MockVerifierJudge(default=VerifierVerdict(supported=True, reason="mock 裁决"))
        ),
        now=lambda: datetime.now(UTC),
    )


class TestRunEvalReal:
    def test_unlocked_runs_matrix_with_injected_faces(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """真实档兑现：解锁后入口实跑（注入替身面），矩阵 + 判定 + 双产物落盘。"""
        monkeypatch.setenv(REAL_TIER_ENV, "1")
        golden_root = _write_tiny_golden(tmp_path / "golden")
        with _memory_session() as session:
            rows, json_path, md_path = run_eval_real(
                db_session=session,
                golden_root=golden_root,
                out_dir=tmp_path / "out",
                n_runs=1,
                profile_names=("t-a", "t-b"),
                components_factory=_fake_factory,
                judge=MockJudge(verdict="top3", reason="judge 替身判定"),
            )
        assert len(rows) == 2 * 1 * 1
        assert {row.model for row in rows} == {"t-a", "t-b"}
        assert {row.the_set for row in rows} == {"dev"}
        # 规则未命中 → judge 回退分支被消费（防自评两级的真实接线）
        assert {row.judged_by for row in rows} == {"judge"}
        assert {row.verdict for row in rows} == {"top3"}
        assert json_path.exists() and md_path.exists()
        assert "t-a" in md_path.read_text(encoding="utf-8")

    def test_default_judge_missing_env_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """judge env 缺项构造期 fail-fast（LLMConfigError，禁静默回退 mock）。"""
        monkeypatch.setenv(REAL_TIER_ENV, "1")
        # 断网守卫（A1）：env 文件指到空临时文件，防 run_eval_real 内部
        # load_env_file() 把本机真实 .env 的 judge 配置补回来触发真实调用
        (tmp_path / "empty.env").write_text("", encoding="utf-8")
        monkeypatch.setenv(ENV_FILE_ENV, str(tmp_path / "empty.env"))
        for key in (
            "ONCALL_JUDGE_LLM_BASE_URL",
            "ONCALL_JUDGE_LLM_MODEL",
            "ONCALL_JUDGE_LLM_API_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        golden_root = _write_tiny_golden(tmp_path / "golden")
        with pytest.raises(LLMConfigError):
            run_eval_real(golden_root=golden_root, db_session=_memory_session())

    def test_real_profile_names_are_env_driven_labels(self) -> None:
        """G8：profile 名是档位标签非模型名（模型名只活在 env / 报告回填）。"""
        assert len(REAL_PROFILE_NAMES) == 2
        assert all("-" in name or name.replace("-", "").isalnum() for name in REAL_PROFILE_NAMES)


class TestEnvFile:
    def test_load_env_file_fills_missing_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# 注释行\n"
            "\n"
            "ONCALL_JUDGE_LLM_MODEL=kimi-k2.7-code\n"
            'ONCALL_JUDGE_LLM_API_KEY="sk-quoted"\n'
            "MALFORMED LINE WITHOUT EQUALS\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ONCALL_JUDGE_LLM_MODEL", "already-set")
        monkeypatch.delenv("ONCALL_JUDGE_LLM_API_KEY", raising=False)
        loaded = load_env_file(env_file)
        assert loaded["ONCALL_JUDGE_LLM_API_KEY"] == "sk-quoted"
        assert os.environ["ONCALL_JUDGE_LLM_MODEL"] == "already-set"  # 不覆盖已有 env
        assert os.environ["ONCALL_JUDGE_LLM_API_KEY"] == "sk-quoted"

    def test_env_file_path_overridable_via_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = tmp_path / "custom.env"
        custom.write_text("K=V\n", encoding="utf-8")
        monkeypatch.setenv(ENV_FILE_ENV, str(custom))
        monkeypatch.delenv("K", raising=False)
        assert load_env_file()["K"] == "V"


class TestMain:
    def test_main_mock_runs_clean(self, tmp_path: Path) -> None:
        assert main(["mock", "--out-dir", str(tmp_path)]) == 0

    def test_main_real_locked_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(REAL_TIER_ENV, raising=False)
        assert main(["real"]) != 0  # 门槛未解锁：非零退出，不静默

    def test_main_default_is_mock(self, tmp_path: Path) -> None:
        env = dict(os.environ)
        try:
            assert main(["--out-dir", str(tmp_path)]) == 0
        finally:
            os.environ.clear()
            os.environ.update(env)
