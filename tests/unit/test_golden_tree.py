"""黄金集 dev/holdout 双集目录树校验测试（TDD，issue 05 / D-18）。

双集隔离（architecture.md §6）：datasets/golden/dev/*（调参可看）vs
datasets/golden/holdout/*（终评专用，调参禁看）。
目录树层规则（schema 层只管单文件）：
  R1 每份文件过 GoldenSet schema，且文件名（去扩展名）== golden.scenario
  R2 成对：slug 必须同时出现在 dev 与 holdout（单向出现 = 采集中断/复制错位）
  R3 run 数下限：dev ≥2，holdout ≥1（每剧本 ×3 = 2+1，设计口径拆分）
  R4 防复制：同一 slug 的 dev 与 holdout 不得出现相同 started_at 的 run
  R5 空树允许（分批采集中），已存在的文件必须合法
  R6 交叉校验（传入 scenarios_dir 时）：timeline alertname ⊆ expected_alerts
     + 三标注字段与 scenario.yaml 逐字一致（P1/P2 防线，M0-05 校准设计）
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from oncall.scenarios import (
    DEV_MIN_RUNS,
    HOLDOUT_MIN_RUNS,
    ScenarioValidationError,
    load_golden_tree,
)


def yaml_dump(data: dict) -> str:
    return yaml.safe_dump(data, allow_unicode=True)


def make_run(started: str, fired: str, resolved: str, recovered: str) -> dict:
    return {
        "started_at": started,
        "recovered_at": recovered,
        "alert_timeline": [
            {
                "alert_name": "DemoTasksHighLatency",
                "labels": {"scenario": "slow-sql"},
                "fired_at": fired,
                "resolved_at": resolved,
            }
        ],
    }


def make_golden(scenario: str, run_starts: list[str]) -> dict:
    runs = []
    for s in run_starts:
        # fired = started+2min, resolved/recovered = started+10min（相对时刻合法即可）
        h, m, _ = s.split("T")[1].split(":")
        fired = f"{s.split('T')[0]}T{h}:{int(m) + 2:02d}:00Z"
        end = f"{s.split('T')[0]}T{h}:{int(m) + 10:02d}:00Z"
        runs.append(make_run(s, fired, end, end))
    return {
        "scenario": scenario,
        "root_cause": "注入会话对 tasks 表持 LOCK TABLES WRITE 并 SLEEP",
        "investigation_path": ["查 /tasks P95", "查连接池占用", "查 MySQL processlist"],
        "remediation": "KILL 持锁会话释放表锁",
        "runs": runs,
    }


def build_tree(tmp_path, dev: dict[str, list[str]], holdout: dict[str, list[str]]) -> str:
    """按 {slug: run_starts} 生成 dev/holdout 目录树，返回根路径字符串。"""
    root = tmp_path / "golden"
    for split, mapping in (("dev", dev), ("holdout", holdout)):
        d = root / split
        d.mkdir(parents=True, exist_ok=True)
        for slug, starts in mapping.items():
            (d / f"{slug}.yaml").write_text(yaml_dump(make_golden(slug, starts)), encoding="utf-8")
    return str(root)


class TestGoldenTree:
    def test_valid_pair_tree_passes(self, tmp_path):
        root = build_tree(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T12:00:00Z"]},
        )
        tree = load_golden_tree(root)
        assert set(tree) == {"dev", "holdout"}
        assert len(tree["dev"]["slow-sql"].runs) == DEV_MIN_RUNS
        assert len(tree["holdout"]["slow-sql"].runs) == HOLDOUT_MIN_RUNS

    def test_dev_needs_two_runs(self, tmp_path):
        root = build_tree(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T12:00:00Z"]},
        )
        with pytest.raises(ScenarioValidationError) as exc:
            load_golden_tree(root)
        assert "dev" in str(exc.value) and "slow-sql" in str(exc.value)

    def test_unpaired_slug_dev_only_rejected(self, tmp_path):
        root = build_tree(
            tmp_path, dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"]}, holdout={}
        )
        with pytest.raises(ScenarioValidationError) as exc:
            load_golden_tree(root)
        assert "slow-sql" in str(exc.value)

    def test_unpaired_slug_holdout_only_rejected(self, tmp_path):
        root = build_tree(tmp_path, dev={}, holdout={"slow-sql": ["2026-09-06T12:00:00Z"]})
        with pytest.raises(ScenarioValidationError) as exc:
            load_golden_tree(root)
        assert "slow-sql" in str(exc.value)

    def test_same_run_in_both_splits_rejected(self, tmp_path):
        # 防"复制一份当两集"：同一 run（started_at 相同）不得同时进 dev 与 holdout
        root = build_tree(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T12:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T10:00:00Z"]},
        )
        with pytest.raises(ScenarioValidationError) as exc:
            load_golden_tree(root)
        assert "started_at" in str(exc.value)

    def test_filename_must_match_scenario_field(self, tmp_path):
        build_tree(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T12:00:00Z"]},
        )
        # 文件名与 scenario 字段错位（复制后忘改 scenario）
        (tmp_path / "golden" / "dev" / "slow-sql.yaml").write_text(
            yaml_dump(make_golden("cpu-spike", ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"])),
            encoding="utf-8",
        )
        with pytest.raises(ScenarioValidationError) as exc:
            load_golden_tree(tmp_path / "golden")
        assert "slow-sql" in str(exc.value)

    def test_invalid_schema_file_reports_path(self, tmp_path):
        root = build_tree(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T12:00:00Z"]},
        )
        p = tmp_path / "golden" / "dev" / "slow-sql.yaml"
        data = make_golden("slow-sql", ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"])
        del data["root_cause"]
        p.write_text(yaml_dump(data), encoding="utf-8")
        with pytest.raises(ScenarioValidationError) as exc:
            load_golden_tree(root)
        assert "slow-sql.yaml" in str(exc.value)

    def test_empty_tree_allowed(self, tmp_path):
        # 分批采集：目录还没建/为空不算错
        root = build_tree(tmp_path, dev={}, holdout={})
        assert load_golden_tree(root) == {"dev": {}, "holdout": {}}

    def test_missing_dirs_allowed(self, tmp_path):
        root = str(tmp_path / "golden")  # 目录不存在
        assert load_golden_tree(root) == {"dev": {}, "holdout": {}}

    def test_stray_yaml_outside_split_dirs_ignored(self, tmp_path):
        root = build_tree(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T12:00:00Z"]},
        )
        (tmp_path / "golden" / "README.md").write_text("说明", encoding="utf-8")
        tree = load_golden_tree(root)
        assert len(tree["dev"]) == 1


def make_spec_yaml(slug: str, alerts: list[str]) -> str:
    """最小合法 scenario.yaml（D-18 十字段），供 R6 交叉校验测试用。"""
    return yaml_dump(
        {
            "name": slug,
            "fault_type": "测试故障",
            "category": "资源类",
            "inject": f"chaos/scenarios/xx-{slug}/inject.sh",
            "inject_method": "custom-script",
            "cleanup": f"chaos/scenarios/xx-{slug}/cleanup.sh",
            "expected_alerts": alerts,
            "expected_root_cause": "注入会话对 tasks 表持 LOCK TABLES WRITE 并 SLEEP",
            "expected_investigation_path": [
                "查 /tasks P95",
                "查连接池占用",
                "查 MySQL processlist",
            ],
            "expected_remediation": "KILL 持锁会话释放表锁",
        }
    )


def build_tree_with_specs(
    tmp_path, dev: dict[str, list[str]], holdout: dict[str, list[str]], alerts: dict[str, list[str]]
) -> str:
    """生成 golden 树 + 配套 scenarios 目录（scenarios_dir 传给 load_golden_tree）。"""
    root = Path(build_tree(tmp_path, dev=dev, holdout=holdout))
    for slug, al in alerts.items():
        d = tmp_path / "scenarios" / f"xx-{slug}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "scenario.yaml").write_text(make_spec_yaml(slug, al), encoding="utf-8")
    return str(root)


class TestR6CrossValidation:
    """R6：timeline ⊆ expected_alerts + 三标注字段逐字一致（P1/P2 防线）。"""

    def test_timeline_subset_of_expected_passes(self, tmp_path):
        root = build_tree_with_specs(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T12:00:00Z"]},
            alerts={"slow-sql": ["DemoTasksHighLatency", "DemoDbPoolSaturated"]},
        )
        tree = load_golden_tree(root, scenarios_dir=tmp_path / "scenarios")
        assert len(tree["dev"]) == 1

    def test_timeline_alert_outside_expected_rejected(self, tmp_path):
        # P1 复现样本：预标注声称级联告警，实测时间线里从未出现
        root = build_tree_with_specs(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T12:00:00Z"]},
            alerts={"slow-sql": ["DemoQueueDepthHigh"]},
        )
        with pytest.raises(ScenarioValidationError) as exc:
            load_golden_tree(root, scenarios_dir=tmp_path / "scenarios")
        msg = str(exc.value)
        assert "slow-sql" in msg and "DemoTasksHighLatency" in msg

    def test_missing_scenario_spec_rejected(self, tmp_path):
        # golden 引用了不存在的剧本 = 标注错位
        build_tree(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T12:00:00Z"]},
        )
        (tmp_path / "scenarios").mkdir()  # 目录存在但没有任何剧本
        with pytest.raises(ScenarioValidationError) as exc:
            load_golden_tree(tmp_path / "golden", scenarios_dir=tmp_path / "scenarios")
        assert "slow-sql" in str(exc.value)

    def test_missing_scenarios_dir_rejected(self, tmp_path):
        # scenarios_dir 路径写错（不存在）要在 R6 入口就暴露，不能静默跳过校验
        root = build_tree(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T12:00:00Z"]},
        )
        with pytest.raises(ScenarioValidationError) as exc:
            load_golden_tree(root, scenarios_dir=tmp_path / "no-such-dir")
        assert "scenarios_dir 不存在" in str(exc.value)

    def test_without_scenarios_dir_skips_cross_check(self, tmp_path):
        # 向后兼容：不传 scenarios_dir 时只做树校验（旧调用方零破坏）
        root = build_tree(
            tmp_path,
            dev={"slow-sql": ["2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z"]},
            holdout={"slow-sql": ["2026-09-06T12:00:00Z"]},
        )
        assert load_golden_tree(root)["dev"]["slow-sql"].scenario == "slow-sql"
