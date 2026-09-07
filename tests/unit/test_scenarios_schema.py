"""scenario.yaml 与黄金集 YAML 的 schema 校验器测试（TDD，issue 02 / D-12 契约）。

D-12 冻结的九字段契约：name / fault_type / category / inject / inject_method /
cleanup / expected_alerts / expected_root_cause / expected_remediation。
D-18 扩展为十字段：新增 expected_investigation_path（排查路径唯一权威源，
golden 逐字复制——修复 P2 跨剧本复用的结构缺口）。
黄金集：scenario + runs[>=3]（alert_timeline[>=1]）+ root_cause + investigation_path + remediation。
golden_matches_scenario 从"只比名字"升级为三标注字段逐字一致性校验（P2 防线）。
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
        "expected_investigation_path": ["查 worker CPU 指标确认饱和"],
        "expected_remediation": "停止注入并重启 worker 容器",
    }
    base.update(overrides)
    return base


def make_golden(
    scenario: str = "cpu-spike",
    n_runs: int = 3,
    *,
    root_cause_override: str | None = None,
    path_override: list[str] | None = None,
    remediation_override: str | None = None,
) -> dict:
    return {
        "scenario": scenario,
        # 三标注字段与 make_scenario 的 expected_* 逐字一致（D-18 同源纪律）
        "root_cause": root_cause_override or "worker 容器 CPU 被 pumba 打满",
        "investigation_path": path_override or ["查 worker CPU 指标确认饱和"],
        "remediation": remediation_override or "停止注入并重启 worker 容器",
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


class TestExpectedInvestigationPath:
    """D-18 十字段契约：排查路径权威源前移到 scenario.yaml（P2 修复）。"""

    def test_missing_field_reports_field_name(self):
        bad = make_scenario()
        del bad["expected_investigation_path"]
        with pytest.raises(ValidationError) as exc:
            ScenarioSpec.model_validate(bad)
        assert "expected_investigation_path" in str(exc.value)

    def test_empty_list_rejected(self):
        with pytest.raises(ValidationError):
            ScenarioSpec.model_validate(make_scenario(expected_investigation_path=[]))

    def test_step_cannot_be_blank(self):
        with pytest.raises(ValidationError):
            ScenarioSpec.model_validate(make_scenario(expected_investigation_path=["查 CPU", "  "]))


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

    def test_zero_runs_rejected(self):
        # schema 层只管单文件 ≥1；"每剧本 ×3"拆分口径（dev2+holdout1）由目录树层强制
        with pytest.raises(ValidationError) as exc:
            GoldenSet.model_validate(make_golden(n_runs=0))
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

    def test_resolved_before_fired_rejected(self):
        # 时间序校验：标注员填反了要在 M0 拦截，不能等到 M7 判分才发现
        bad = make_golden()
        bad["runs"][0]["alert_timeline"][0]["resolved_at"] = "2026-09-06T10:00:00Z"  # fired 10:02
        with pytest.raises(ValidationError) as exc:
            GoldenSet.model_validate(bad)
        assert "resolved_at" in str(exc.value)

    def test_recovered_before_started_rejected(self):
        bad = make_golden()
        bad["runs"][0]["recovered_at"] = "2026-09-06T09:59:00Z"  # started 是 10:00
        with pytest.raises(ValidationError) as exc:
            GoldenSet.model_validate(bad)
        assert "recovered_at" in str(exc.value)

    def test_recovered_equal_started_passes(self):
        ok = make_golden()
        ok["runs"][0]["recovered_at"] = ok["runs"][0]["started_at"]
        GoldenSet.model_validate(ok)  # 不抛即通过

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


class TestAnnotationConsistency:
    """D-18：golden_matches_scenario 从"只比名字"升级为三标注字段逐字一致（P2 防线）。"""

    def test_root_cause_mismatch_rejected(self):
        spec = ScenarioSpec.model_validate(make_scenario())
        golden = GoldenSet.model_validate(
            make_golden(root_cause_override="CPU 飙高是流量太大（错误推断）")
        )
        assert not golden_matches_scenario(golden, spec)

    def test_remediation_mismatch_rejected(self):
        spec = ScenarioSpec.model_validate(make_scenario())
        golden = GoldenSet.model_validate(make_golden(remediation_override="加机器（不对症）"))
        assert not golden_matches_scenario(golden, spec)

    def test_investigation_path_step_mismatch_rejected(self):
        # 跨剧本复制 path 的复现样本：单条步骤不同即不一致
        spec = ScenarioSpec.model_validate(make_scenario())
        golden = GoldenSet.model_validate(
            make_golden(path_override=["查队列深度（从别的剧本抄来的）"])
        )
        assert not golden_matches_scenario(golden, spec)


class TestAlertEventClassification:
    """D-21（issue 06）：timeline 条目可选字段 classification（incident 缺省 / false_positive）。

    extra="forbid" 下不扩模型连既有校验都会拒新文件——本组测试是扩模型的复现红测试。
    """

    def test_explicit_false_positive_accepted(self, tmp_path):
        data = make_golden()
        data["runs"][0]["alert_timeline"][0]["classification"] = "false_positive"
        f = tmp_path / "golden.yaml"
        f.write_text(yaml_dump(data), encoding="utf-8")
        gs = load_golden_set_file(f)
        assert gs.runs[0].alert_timeline[0].classification == "false_positive"

    def test_missing_classification_defaults_to_incident(self):
        # 向后兼容：既有 11 剧本 golden 零改动，缺省语义 = incident
        gs = GoldenSet.model_validate(make_golden())
        assert gs.runs[0].alert_timeline[0].classification == "incident"

    def test_classification_incident_accepted(self, tmp_path):
        data = make_golden()
        data["runs"][0]["alert_timeline"][0]["classification"] = "incident"
        f = tmp_path / "golden.yaml"
        f.write_text(yaml_dump(data), encoding="utf-8")
        gs = load_golden_set_file(f)
        assert gs.runs[0].alert_timeline[0].classification == "incident"

    def test_unknown_classification_value_rejected(self):
        # 取值冻结在 D-21 两态；拼错/扩值都要在 M0 拦截
        bad = make_golden()
        bad["runs"][0]["alert_timeline"][0]["classification"] = "maybe_fp"
        with pytest.raises(ValidationError) as exc:
            GoldenSet.model_validate(bad)
        assert "classification" in str(exc.value)


class TestFalsePositiveCategory:
    """D-21（issue 06）：ScenarioCategory 扩第七类「误报类」——误报剧本无处安放的红测试。"""

    def test_false_positive_category_accepted(self):
        spec = ScenarioSpec.model_validate(make_scenario(category="误报类"))
        assert spec.category == "误报类"
