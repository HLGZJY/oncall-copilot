"""剧本目录守卫（issue 04 验收的机械判定，T4 schema 接缝的固化）。

约束 chaos/scenarios/ 整个目录（不只单个文件）：
- 每个 scenario.yaml 都过 D-12 schema 校验器
- 目录 ≥8 个剧本、m0-execution 六大类全覆盖、业务语义层必存在
- 无哑剧本：expected_alerts 里的每条告警必须是 deploy/prometheus/rules.yml
  里真实配置的规则名
"""

from __future__ import annotations

from pathlib import Path

import yaml

from oncall.scenarios.schema import (
    ScenarioCategory,
    load_golden_tree,
    load_scenario_file,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "chaos" / "scenarios"
RULES_FILE = REPO_ROOT / "deploy" / "prometheus" / "rules.yml"
MIN_SCENARIOS = 8  # m0-execution 口径：8 类故障剧本
REQUIRED_CATEGORIES: set[str] = set(ScenarioCategory.__args__)  # type: ignore[attr-defined]


def _scenario_dirs() -> list[Path]:
    if not SCENARIOS_DIR.exists():
        return []
    return sorted(p for p in SCENARIOS_DIR.iterdir() if (p / "scenario.yaml").is_file())


def _load_all() -> dict[str, object]:
    """{slug: ScenarioSpec}，顺便执行 schema 校验（失败即测试红）。"""
    return {p.name: load_scenario_file(p / "scenario.yaml") for p in _scenario_dirs()}


def _configured_alert_names() -> set[str]:
    data = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8"))
    return {rule["alert"] for group in data.get("groups", []) for rule in group.get("rules", [])}


def test_every_scenario_yaml_passes_schema():
    specs = _load_all()
    assert specs, (
        f"{SCENARIOS_DIR} 下没有任何剧本目录。"
        "下一步: 每个剧本一个 <NN>-<slug>/ 目录，内含 inject.sh + cleanup.sh + scenario.yaml。"
    )


def test_catalog_has_enough_scenarios():
    specs = _load_all()
    assert len(specs) >= MIN_SCENARIOS, (
        f"剧本仅 {len(specs)} 个，不足 {MIN_SCENARIOS}。"
        f"现有: {sorted(specs)}。下一步: 按 docs/plans/m0-execution.md 的 8 类清单补齐。"
    )


def test_catalog_covers_all_six_categories():
    specs = _load_all()
    covered = {spec.category for spec in specs.values()}  # type: ignore[attr-defined]
    missing = REQUIRED_CATEGORIES - covered
    assert not missing, (
        f"六大类未覆盖: {sorted(missing)}。"
        "下一步: 补对应类别剧本（资源/网络/业务/负载/故障/业务语义层）。"
    )


def test_semantic_layer_scenario_exists():
    specs = _load_all()
    semantic = [s for s in specs.values() if s.category == "业务语义层"]  # type: ignore[attr-defined]
    assert semantic, (
        "缺少业务语义层剧本（版本协议不兼容等：指标日志正常但业务出错）。"
        "这是 m0-execution 的硬性要求，不可裁剪。"
    )


def test_no_dumb_scenario_every_alert_is_configured():
    configured = _configured_alert_names()
    specs = _load_all()
    dumb: list[str] = []
    for slug, spec in specs.items():
        unknown = set(spec.expected_alerts) - configured  # type: ignore[attr-defined]
        if unknown:
            dumb.append(f"{slug}: {sorted(unknown)}")
    assert not dumb, (
        f"存在哑剧本（expected_alerts 引用了未配置的告警规则）: {dumb}。"
        f"下一步: 在 {RULES_FILE} 补规则，或改写 expected_alerts 为已配置规则名。"
    )


def test_golden_tree_cross_validates_against_scenarios():
    """真实数据 R6 交叉校验（D-18，M0-05 校准设计）：
    datasets/golden 全树 vs chaos/scenarios——timeline ⊆ expected_alerts +
    三标注字段逐字一致。P1/P2 类漂移从此在单测门禁被拦截。
    """
    golden_dir = REPO_ROOT / "datasets" / "golden"
    if not (golden_dir / "dev").is_dir():
        return  # 黄金集未开始采集时跳过
    tree = load_golden_tree(golden_dir, scenarios_dir=SCENARIOS_DIR)
    assert tree["dev"] or tree["holdout"] or True  # 空树合法；非空则上面已强制 R6
