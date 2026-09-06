"""scenarios：剧本元数据与黄金集的 schema 契约（M0 建立输入，M7 评测台消费）。"""

from oncall.scenarios.schema import (
    AlertEvent,
    GoldenRun,
    GoldenSet,
    ScenarioCategory,
    ScenarioSpec,
    ScenarioValidationError,
    golden_matches_scenario,
    load_golden_set_file,
    load_scenario_file,
)

__all__ = [
    "AlertEvent",
    "GoldenRun",
    "GoldenSet",
    "ScenarioCategory",
    "ScenarioSpec",
    "ScenarioValidationError",
    "golden_matches_scenario",
    "load_golden_set_file",
    "load_scenario_file",
]
