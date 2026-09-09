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
from pathlib import Path

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.eval.entry import (
    MOCK_PROFILE_NAMES,
    REAL_TIER_DEFERRED_MSG,
    main,
    run_eval_real,
    run_mock_eval,
)
from oncall.eval.runner import REAL_TIER_ENV


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

    def test_unlocked_entry_reachable_but_deferred(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """解锁后入口可达、不真跑：issue 07 落地前显式 deferred 信号。"""
        monkeypatch.setenv(REAL_TIER_ENV, "1")
        with pytest.raises(NotImplementedError, match=REAL_TIER_DEFERRED_MSG):
            run_eval_real()


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
