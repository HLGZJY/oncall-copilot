"""M7-T1 验收 · golden 加载器 + 三条防泄漏守卫（m7 issue 01 / D-59 + R6）。

防泄漏纪律（M2 issue 03/05 先例）：holdout 未显式解锁加载即 fail；
few-shot 源含 3 验证剧本即 fail；标注完整性缺 root_cause/期望处置即 fail。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from oncall.classify.llm.fewshot import VALIDATION_SCENARIO_SLUGS
from oncall.eval.golden import (
    FewShotLeakError,
    GoldenAnnotationError,
    GoldenScenario,
    HoldoutLockedError,
    filter_fewshot_scenarios,
    load_golden_dir,
)

REAL_DEV = Path("datasets/golden/dev")


# ── 真实 dev 集（12 剧本）──


def test_load_real_dev_set():
    """真实 dev 12 剧本全量加载，标注完整性天然通过（已双签）。"""
    scenarios = load_golden_dir(REAL_DEV, the_set="dev")
    assert len(scenarios) == 12
    slugs = {s.scenario for s in scenarios}
    assert "cpu-spike" in slugs and "slow-sql" in slugs


# ── 守卫①：holdout 未显式解锁即 fail ──


def test_holdout_locked_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """holdout 默认锁定：未设解锁 env 加载即 HoldoutLockedError（防泄漏第一守卫）。"""
    monkeypatch.delenv("ONCALL_M7_HOLDOUT_UNLOCK", raising=False)
    _mk_holdout(tmp_path)
    with pytest.raises(HoldoutLockedError):
        load_golden_dir(tmp_path, the_set="holdout")


def test_holdout_unlocked_with_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """显式 ONCALL_M7_HOLDOUT_UNLOCK=1 才允许加载 holdout（最终评测通道）。"""
    monkeypatch.setenv("ONCALL_M7_HOLDOUT_UNLOCK", "1")
    _mk_holdout(tmp_path)
    scenarios = load_golden_dir(tmp_path, the_set="holdout")
    assert len(scenarios) == 1


def _mk_holdout(tmp_path: Path) -> None:
    (tmp_path / "oom-kill.yaml").write_text(
        yaml.safe_dump(
            {
                "scenario": "oom-kill",
                "root_cause": "容器内存超限被 OOMKill",
                "remediation": "调高内存限制并重启",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


# ── 守卫②：few-shot 源禁含 3 验证剧本 ──


def test_fewshot_filter_excludes_validation_slugs():
    """filter_fewshot_scenarios 排除 3 验证剧本（复用 fewshot.py 冻结名单，勿重造）。"""
    scenarios = load_golden_dir(REAL_DEV, the_set="dev")
    keep, excluded = filter_fewshot_scenarios(scenarios)
    assert {s.scenario for s in excluded} == set(VALIDATION_SCENARIO_SLUGS)
    assert len(keep) == 12 - len(VALIDATION_SCENARIO_SLUGS)


def test_fewshot_assert_raises_on_leak():
    """assert_fewshot_clean：源里混入验证剧本即 FewShotLeakError。"""
    with pytest.raises(FewShotLeakError):
        GoldenScenario.assert_fewshot_clean(["slow-sql"])


# ── 守卫③：标注完整性 ──


def test_missing_root_cause_rejected(tmp_path: Path):
    """缺 root_cause 标注 → GoldenAnnotationError（无标注不可评）。"""
    (tmp_path / "bad.yaml").write_text(
        yaml.safe_dump({"scenario": "bad-scenario", "remediation": "x"}, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(GoldenAnnotationError):
        load_golden_dir(tmp_path, the_set="dev")


def test_missing_scenario_rejected(tmp_path: Path):
    """缺 scenario 字段 → GoldenAnnotationError。"""
    (tmp_path / "bad.yaml").write_text(
        yaml.safe_dump({"root_cause": "x", "remediation": "y"}, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(GoldenAnnotationError):
        load_golden_dir(tmp_path, the_set="dev")


def test_duplicate_scenario_rejected(tmp_path: Path):
    """重复 scenario slug → GoldenAnnotationError（评测矩阵行不可歧义）。"""
    body = {"scenario": "dup", "root_cause": "x", "remediation": "y"}
    for i in (1, 2):
        (tmp_path / f"dup{i}.yaml").write_text(
            yaml.safe_dump(body, allow_unicode=True), encoding="utf-8"
        )
    with pytest.raises(GoldenAnnotationError):
        load_golden_dir(tmp_path, the_set="dev")
