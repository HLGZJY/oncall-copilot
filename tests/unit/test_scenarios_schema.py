"""scenario.yaml 与黄金集 YAML 的 schema 校验器测试（TDD，issue 02 / D-12 契约）。

D-12 冻结的九字段契约：name / fault_type / category / inject / inject_method /
cleanup / expected_alerts / expected_root_cause / expected_remediation。
黄金集：scenario + runs[>=3]（alert_timeline[>=1]）+ root_cause + investigation_path + remediation。
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from oncall.scenarios import (
    GoldenSet,
    ScenarioSpec,
    ScenarioValidationError,
    golden_matches_scenario,
    load_golden_set_file,
    load_scenario_file,
)


def yaml_dump(data: dict) -> str:
    return yaml.safe_dump(data, allow_unicode=True)


EXPECTED_GOLDEN_RUNS = 3  # 设计口径：每剧本 x3 run


def make_scenario(**overrides) -> dict:
    base = {
        "name": "cpu-spike",
        "fault_type": "CPU 飙高",
        "category": "资源类",
        "inject": "chaos/scenarios/01-cpu-spike/inject.sh",
        "inject_method": "pumba",
        "cleanup": "chaos/scenarios/01-cpu-spike/cleanup.sh",
        "expected_alerts": ["DemoWorkerCPUHigh"],
        "expected_root_cause": "worker 容器 CPU 被 pumba 打满",
        "expected_remediation": "停止注入并重启 worker 容器",
    }
    base.update(overrides)
    return base


def make_golden(scenario: str = "cpu-spike", n_runs: int = 3) -> dict:
    return {
        "scenario": scenario,
        "root_cause": "worker 容器 CPU 饱和（pumba 注入）",
        "investigation_path": ["查 QPS 排除流量因素", "查 worker CPU 指标确认饱和"],
        "remediation": "停止 pumba 注入并重启 worker",
        "runs": [
            {
                "started_at": "2026-09-06T10:00:00Z",
                "recovered_at": "2026-09-06T10:08:00Z",
                "alert_timeline": [
                    {
                        "alert_name": "DemoWorkerCPUHigh",
                        "labels": {"service": "worker"},
                        "fired_at": "2026-09-06T10:02:00Z",
                        "resolved_at": "2026-09-06T10:08:00Z",
                    }
                ],
            }
            for _ in range(n_runs)
        ],
    }


class TestScenarioSpec:
    def test_valid_scenario_passes(self):
        spec = ScenarioSpec.model_validate(make_scenario())
        assert spec.name == "cpu-spike"
        assert spec.expected_alerts == ["DemoWorkerCPUHigh"]

    def test_missing_required_field_reports_field_name(self):
        bad = make_scenario()
        del bad["expected_root_cause"]
        with pytest.raises(ValidationError) as exc:
            ScenarioSpec.model_validate(bad)
        assert "expected_root_cause" in str(exc.value)

    def test_unknown_category_rejected(self):
        with pytest.raises(ValidationError) as exc:
            ScenarioSpec.model_validate(make_scenario(category="天气类"))
        assert "category" in str(exc.value)

    def test_unknown_inject_method_rejected(self):
        with pytest.raises(ValidationError) as exc:
            ScenarioSpec.model_validate(make_scenario(inject_method="念咒"))
        assert "inject_method" in str(exc.value)

    def test_name_must_be_slug(self):
        with pytest.raises(ValidationError):
            ScenarioSpec.model_validate(make_scenario(name="CPU Spike!"))

    def test_empty_expected_alerts_rejected(self):
        with pytest.raises(ValidationError):
            ScenarioSpec.model_validate(make_scenario(expected_alerts=[]))

    def test_extra_fields_forbidden(self):
        # 契约冻结：多打一个字段（typo）应当炸，而不是被静默吞掉
        with pytest.raises(ValidationError) as exc:
            ScenarioSpec.model_validate(make_scenario(expeceted_alerts=["X"]))
        assert "expeceted_alerts" in str(exc.value)

    @pytest.mark.parametrize(
        ("field", "value"),
        [("fault_type", ""), ("inject", ""), ("cleanup", ""), ("expected_root_cause", "")],
    )
    def test_empty_strings_rejected(self, field, value):
        with pytest.raises(ValidationError):
            ScenarioSpec.model_validate(make_scenario(**{field: value}))


class TestGoldenSet:
    def test_valid_golden_set_passes(self):
        gs = GoldenSet.model_validate(make_golden())
        assert gs.scenario == "cpu-spike"
        assert len(gs.runs) == EXPECTED_GOLDEN_RUNS
        assert gs.runs[0].alert_timeline[0].alert_name == "DemoWorkerCPUHigh"

    def test_missing_root_cause_reports_field_name(self):
        bad = make_golden()
        del bad["root_cause"]
        with pytest.raises(ValidationError) as exc:
            GoldenSet.model_validate(bad)
        assert "root_cause" in str(exc.value)

    def test_fewer_than_three_runs_rejected(self):
        # 设计口径：每剧本 ×3 run；少于 3 无法支撑 Top-1/Top-3 统计
        with pytest.raises(ValidationError) as exc:
            GoldenSet.model_validate(make_golden(n_runs=2))
        assert "runs" in str(exc.value)

    def test_empty_alert_timeline_rejected(self):
        bad = make_golden()
        bad["runs"][0]["alert_timeline"] = []
        with pytest.raises(ValidationError):
            GoldenSet.model_validate(bad)

    def test_bad_timestamp_rejected(self):
        bad = make_golden()
        bad["runs"][0]["alert_timeline"][0]["fired_at"] = "昨天下午"
        with pytest.raises(ValidationError):
            GoldenSet.model_validate(bad)

    def test_extra_fields_forbidden(self):
        bad = make_golden()
        bad["expected_root_cause_x"] = "typo"
        with pytest.raises(ValidationError):
            GoldenSet.model_validate(bad)


class TestFileLoading:
    """load_*_file：路径上下文 + YAML 解析错误的明确报错（覆盖 M7 批量扫描场景）。"""

    def test_load_scenario_file_roundtrip(self, tmp_path):
        f = tmp_path / "scenario.yaml"
        f.write_text(yaml_dump(make_scenario()), encoding="utf-8")
        spec = load_scenario_file(f)
        assert spec.name == "cpu-spike"

    def test_load_scenario_file_missing_field_reports_path(self, tmp_path):
        bad = make_scenario()
        del bad["cleanup"]
        f = tmp_path / "scenario.yaml"
        f.write_text(yaml_dump(bad), encoding="utf-8")
        with pytest.raises(ScenarioValidationError) as exc:
            load_scenario_file(f)
        assert "scenario.yaml" in str(exc.value)
        assert "cleanup" in str(exc.value)

    def test_broken_yaml_reports_parse_error(self, tmp_path):
        f = tmp_path / "scenario.yaml"
        f.write_text("name: [unclosed", encoding="utf-8")
        with pytest.raises(ScenarioValidationError) as exc:
            load_scenario_file(f)
        assert "YAML 解析失败" in str(exc.value)

    def test_non_dict_top_level_rejected(self, tmp_path):
        f = tmp_path / "scenario.yaml"
        f.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ScenarioValidationError) as exc:
            load_scenario_file(f)
        assert "顶层必须是映射" in str(exc.value)

    def test_load_golden_set_file_roundtrip(self, tmp_path):
        f = tmp_path / "golden.yaml"
        f.write_text(yaml_dump(make_golden()), encoding="utf-8")
        gs = load_golden_set_file(f)
        assert gs.scenario == "cpu-spike"


class TestCrossValidation:
    def test_golden_scenario_name_must_match_spec(self):
        spec = ScenarioSpec.model_validate(make_scenario())
        golden = GoldenSet.model_validate(make_golden(scenario="slow-sql"))
        assert not golden_matches_scenario(golden, spec)

    def test_matching_names_pass(self):
        spec = ScenarioSpec.model_validate(make_scenario())
        golden = GoldenSet.model_validate(make_golden(scenario="cpu-spike"))
        assert golden_matches_scenario(golden, spec)
