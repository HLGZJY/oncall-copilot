"""故障剧本与黄金评测集的 schema 校验器（D-12 冻结契约，M7 评测台 runner 的输入）。

字段契约（docs/design/m0-environment-design.md 评审落定，改动须过 decisions.md）：
- scenario.yaml 九字段：name / fault_type / category / inject / inject_method /
  cleanup / expected_alerts / expected_root_cause / expected_remediation
- 黄金集：scenario + runs（单文件 ≥1；目录树层强制 dev≥2 + holdout≥1 = 每剧本 ×3）
  + root_cause + investigation_path + remediation

设计取舍：
- 用 pydantic 严格校验，`extra="forbid"`——多打/拼错字段名直接报错，防止脏数据流进 M7
- category / inject_method 用枚举冻结取值；fault_type 自由文本（CPU 飙高/慢 SQL/…）
- 本模块纯离线（不 import httpx/openai），pytest-socket 断网环境可直接单测
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

ScenarioCategory = Literal["资源类", "网络类", "业务类", "负载类", "故障类", "业务语义层"]
InjectMethod = Literal["pumba", "custom-script", "load-generator"]


class ScenarioValidationError(Exception):
    """schema 校验失败，信息含文件路径与具体字段。"""


class ScenarioSpec(BaseModel):
    """一个故障剧本的元数据（chaos/scenarios/<NN>-<slug>/scenario.yaml）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_PATTERN.pattern, description="剧本 slug，即 M7 引用 ID")
    fault_type: str = Field(min_length=1)
    category: ScenarioCategory
    inject: str = Field(min_length=1, description="注入脚本相对仓库路径或命令")
    inject_method: InjectMethod
    cleanup: str = Field(min_length=1, description="清理脚本，保证剧本可重复执行")
    expected_alerts: list[str] = Field(min_length=1, description="预期触发的告警规则名，防哑剧本")
    expected_root_cause: str = Field(min_length=1)
    expected_remediation: str = Field(min_length=1)


class AlertEvent(BaseModel):
    """黄金集里的一次告警触发/恢复记录。"""

    model_config = ConfigDict(extra="forbid")

    alert_name: str = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)
    fired_at: datetime
    resolved_at: datetime | None = None

    @model_validator(mode="after")
    def _resolved_not_before_fired(self) -> AlertEvent:
        if self.resolved_at is not None and self.resolved_at < self.fired_at:
            msg = f"resolved_at({self.resolved_at}) 早于 fired_at({self.fired_at})，时间序错误"
            raise ValueError(msg)
        return self


class GoldenRun(BaseModel):
    """黄金集的一次执行记录（同一剧本跑 N 遍取其一）。"""

    model_config = ConfigDict(extra="forbid")

    started_at: datetime
    recovered_at: datetime | None = None
    alert_timeline: list[AlertEvent] = Field(min_length=1)

    @model_validator(mode="after")
    def _recovered_not_before_started(self) -> GoldenRun:
        if self.recovered_at is not None and self.recovered_at < self.started_at:
            msg = (
                f"recovered_at({self.recovered_at}) 早于 started_at({self.started_at})，时间序错误"
            )
            raise ValueError(msg)
        return self


class GoldenSet(BaseModel):
    """预标注评测基准（datasets/golden/<slug>.yaml），M7 判对错的依据。"""

    model_config = ConfigDict(extra="forbid")

    scenario: str = Field(
        pattern=NAME_PATTERN.pattern, description="必须对应某个 ScenarioSpec.name"
    )
    root_cause: str = Field(min_length=1)
    investigation_path: list[str] = Field(min_length=1)
    remediation: str = Field(min_length=1)
    runs: list[GoldenRun] = Field(
        min_length=1,
        description="单文件 ≥1 run；每剧本 ×3 的拆分口径（dev 2 + holdout 1）由目录树校验强制",
    )


def golden_matches_scenario(golden: GoldenSet, spec: ScenarioSpec) -> bool:
    """黄金集与剧本的对应关系校验（防标注文件与剧本错位）。"""
    return golden.scenario == spec.name


def _load_yaml_file(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"{path}: YAML 解析失败：{exc}"
        raise ScenarioValidationError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{path}: 顶层必须是映射（dict），实际是 {type(data).__name__}"
        raise ScenarioValidationError(msg)
    return data


def load_scenario_file(path: str | Path) -> ScenarioSpec:
    """读取并校验单个 scenario.yaml；失败抛 ScenarioValidationError（含文件路径）。"""
    p = Path(path)
    try:
        return ScenarioSpec.model_validate(_load_yaml_file(p))
    except ValidationError as exc:
        raise ScenarioValidationError(f"{p}: scenario schema 校验失败\n{exc}") from exc


def load_golden_set_file(path: str | Path) -> GoldenSet:
    """读取并校验单个黄金集 YAML；失败抛 ScenarioValidationError（含文件路径）。"""
    p = Path(path)
    try:
        return GoldenSet.model_validate(_load_yaml_file(p))
    except ValidationError as exc:
        raise ScenarioValidationError(f"{p}: 黄金集 schema 校验失败\n{exc}") from exc


DEV_MIN_RUNS = 2
HOLDOUT_MIN_RUNS = 1
_SPLITS = ("dev", "holdout")


def load_golden_tree(root: str | Path) -> dict[str, dict[str, GoldenSet]]:
    """扫描 datasets/golden/{dev,holdout}/<slug>.yaml 双集目录树并校验（issue 05）。

    双集隔离（architecture.md §6）：dev 调参可看，holdout 终评专用。
    目录树层规则（schema 层只管单文件）：
    R1 文件名（去扩展名）必须等于 golden.scenario 字段
    R2 成对：slug 必须同时出现在 dev 与 holdout
    R3 run 数下限：dev ≥2，holdout ≥1（每剧本 ×3 = 2+1 的拆分口径）
    R4 防复制：同一 slug 在 dev 与 holdout 不得出现相同 started_at 的 run
    R5 空树允许（分批采集中），已存在的文件必须合法

    返回 {"dev": {slug: GoldenSet}, "holdout": {slug: GoldenSet}}。
    """
    base = Path(root)
    tree: dict[str, dict[str, GoldenSet]] = {}
    for split in _SPLITS:
        d = base / split
        tree[split] = {}
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            golden = load_golden_set_file(f)
            if golden.scenario != f.stem:  # R1
                msg = f"{f}: 文件名({f.stem}) 与 scenario 字段({golden.scenario})不一致"
                raise ScenarioValidationError(msg)
            tree[split][f.stem] = golden

    slugs = {s for split in _SPLITS for s in tree[split]}
    for slug in sorted(slugs):
        in_dev, in_holdout = slug in tree["dev"], slug in tree["holdout"]
        if in_dev != in_holdout:  # R2
            missing = "holdout" if in_dev else "dev"
            msg = f"{slug}: 黄金集双集不成对——{missing} 目录缺少 {slug}.yaml"
            raise ScenarioValidationError(msg)

        dev_runs, holdout_runs = tree["dev"][slug].runs, tree["holdout"][slug].runs
        if len(dev_runs) < DEV_MIN_RUNS:  # R3
            msg = f"{slug}: dev 集仅 {len(dev_runs)} run，下限 {DEV_MIN_RUNS}（口径 dev2+holdout1）"
            raise ScenarioValidationError(msg)
        if len(holdout_runs) < HOLDOUT_MIN_RUNS:  # R3
            msg = f"{slug}: holdout 集仅 {len(holdout_runs)} run，下限 {HOLDOUT_MIN_RUNS}"
            raise ScenarioValidationError(msg)

        dev_starts = {r.started_at for r in dev_runs}
        overlap = dev_starts & {r.started_at for r in holdout_runs}
        if overlap:  # R4
            msg = (
                f"{slug}: 同一 run(started_at={sorted(overlap)}) 同时出现在 dev 与 holdout"
                "——疑似复制而非独立采集"
            )
            raise ScenarioValidationError(msg)
    return tree
