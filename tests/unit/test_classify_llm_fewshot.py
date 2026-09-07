"""few-shot 样本池 loader 测试（issue 03 / G3 ② + G9 holdout 禁看纪律）。

泄漏守卫是本票的硬纪律断言：
- 3 验证剧本（slow-sql / protocol-mismatch / false-positive-flap）永不在样本池
- loader 只读 dev/ 目录（目录名硬守卫），任何 holdout 路径不可达
- 不复用 load_golden_tree（它会读 holdout split）——源码级守卫断言
- 06 落地 false-positive-flap 后仍被排除（回归断言，测未来态）
"""

from __future__ import annotations

import inspect
from pathlib import Path

import yaml

from oncall.classify.llm import fewshot
from oncall.classify.llm.fewshot import (
    VALIDATION_SCENARIO_SLUGS,
    load_few_shot_samples,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DEV_DIR = REPO_ROOT / "datasets" / "golden" / "dev"


def test_real_dev_pool_excludes_all_validation_scenarios() -> None:
    samples = load_few_shot_samples(REAL_DEV_DIR)
    scenarios = {s.scenario for s in samples}

    assert scenarios.isdisjoint(VALIDATION_SCENARIO_SLUGS)
    assert "slow-sql" not in scenarios
    assert "protocol-mismatch" not in scenarios
    assert "false-positive-flap" not in scenarios


def test_real_dev_pool_sample_shape() -> None:
    """每态 K=2–3：当前只有 incident 缺省类 → 取前 K 条，卡片/结论字段齐全。"""
    samples = load_few_shot_samples(REAL_DEV_DIR)

    assert 0 < len(samples) <= fewshot.DEFAULT_PER_CLASS_K
    for s in samples:
        assert s.verdict == "incident"  # 06 未落地前全为缺省标注
        assert s.reason  # reason 来自 golden root_cause
        assert "labels" in s.alert_card["alert"]
        assert "fired_at" in s.alert_card["alert"]


def make_tmp_dev(tmp_path: Path, scenarios: dict[str, dict]) -> Path:
    """构造 tmp dev 目录（目录名必须是 dev——loader 硬守卫的前提）。"""
    dev_dir = tmp_path / "dev"
    dev_dir.mkdir()
    for slug, payload in scenarios.items():
        (dev_dir / f"{slug}.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
        )
    return dev_dir


def golden(slug: str, **extra: object) -> dict:
    """最小合法 golden 骨架（十字段纪律与本票无关，loader 只消费所需字段）。"""
    return {
        "scenario": slug,
        "root_cause": f"演示根因 {slug}",
        "investigation_path": ["步骤一"],
        "remediation": "演示处置",
        "runs": [
            {
                "started_at": "2026-09-06T12:45:41Z",
                "recovered_at": "2026-09-06T12:48:55Z",
                "alert_timeline": [
                    {
                        "alert_name": "DemoAlert",
                        "labels": {"alertname": "DemoAlert", "severity": "warning"},
                        "fired_at": "2026-09-06T12:47:01.474Z",
                        "resolved_at": "2026-09-06T12:48:46.474Z",
                        **extra,
                    }
                ],
            }
        ],
    }


def test_future_false_positive_scenario_still_excluded(tmp_path: Path) -> None:
    """06 落地后 false-positive-flap（classification: false_positive）仍被排除。"""
    dev_dir = make_tmp_dev(
        tmp_path,
        {
            "cache-avalanche": golden("cache-avalanche"),
            "false-positive-flap": golden("false-positive-flap", classification="false_positive"),
        },
    )

    samples = load_few_shot_samples(dev_dir)

    assert {s.scenario for s in samples} == {"cache-avalanche"}
    assert all(s.verdict == "incident" for s in samples)


def test_classification_field_respected_for_non_validation_slug(tmp_path: Path) -> None:
    """非验证剧本显式标注 false_positive 时正常进池（D-21 可选字段）。"""
    dev_dir = make_tmp_dev(
        tmp_path,
        {
            "cpu-spike": golden("cpu-spike"),
            "threshold-drift": golden("threshold-drift", classification="false_positive"),
        },
    )

    samples = load_few_shot_samples(dev_dir, per_class=2)

    by_verdict = {s.verdict: s for s in samples}
    assert by_verdict["incident"].scenario == "cpu-spike"
    assert by_verdict["false_positive"].scenario == "threshold-drift"
    assert by_verdict["false_positive"].reason == "演示根因 threshold-drift"


def test_per_class_k_and_deterministic_order(tmp_path: Path) -> None:
    """每态取 K 条，按场景名字典序稳定截取（prompt 确定性的前提）。"""
    dev_dir = make_tmp_dev(
        tmp_path,
        {slug: golden(slug) for slug in ("queue-backlog", "oom-kill", "cpu-spike", "db-deadlock")},
    )

    samples = load_few_shot_samples(dev_dir, per_class=2)

    assert [s.scenario for s in samples] == ["cpu-spike", "db-deadlock"]


def test_loader_never_touches_holdout(tmp_path: Path) -> None:
    """兄弟目录 holdout 有独家剧本 → loader(dev) 不读取（双集隔离硬守卫）。"""
    dev_dir = make_tmp_dev(tmp_path, {"cpu-spike": golden("cpu-spike")})
    holdout_dir = tmp_path / "holdout"
    holdout_dir.mkdir()
    (holdout_dir / "holdout-secret.yaml").write_text(
        yaml.safe_dump(golden("holdout-secret"), allow_unicode=True), encoding="utf-8"
    )

    samples = load_few_shot_samples(dev_dir)

    assert {s.scenario for s in samples} == {"cpu-spike"}


def test_loader_rejects_non_dev_directory(tmp_path: Path) -> None:
    """目录名不是 dev 直接拒绝——把 holdout 路径传进来时在入口即失败。"""
    holdout_dir = tmp_path / "holdout"
    holdout_dir.mkdir()
    (holdout_dir / "holdout-secret.yaml").write_text(
        yaml.safe_dump(golden("holdout-secret"), allow_unicode=True), encoding="utf-8"
    )

    try:
        load_few_shot_samples(holdout_dir)
    except ValueError as exc:
        assert "dev" in str(exc)
    else:
        raise AssertionError("loader 必须拒绝非 dev 目录")


def test_loader_does_not_use_golden_tree_loader() -> None:
    """load_golden_tree 会读 holdout split（R2 成对校验），few-shot 禁止复用。"""
    source = inspect.getsource(fewshot)

    assert "load_golden_tree" not in source
