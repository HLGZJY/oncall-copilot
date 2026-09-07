"""故障剧本与黄金评测集的 schema 校验器（D-12 冻结契约，M7 评测台 runner 的输入）。

字段契约（docs/design/m0-environment-design.md 评审落定，改动须过 decisions.md）：
- scenario.yaml 十字段：D-12 九字段 + D-18 expected_investigation_path（排查路径
  唯一权威源，golden 逐字复制）
- 黄金集：scenario + runs（单文件 ≥1；目录树层强制 dev≥2 + holdout≥1 = 每剧本 ×3）
  + root_cause + investigation_path + remediation
- D-21：timeline 条目可选 classification（incident 缺省 / false_positive）

设计取舍：pydantic 严格校验 + `extra="forbid"`（多打/拼错字段名直接报错，防脏数据
流进 M7）；category / inject_method 枚举冻结取值，fault_type 自由文本；纯离线
（不 import httpx/openai），pytest-socket 断网环境可直接单测。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# D-21（issue 06）：扩第七类「误报类」——误报剧本（无真实业务故障）此前六类无处安放
ScenarioCategory = Literal["资源类", "网络类", "业务类", "负载类", "故障类", "业务语义层", "误报类"]
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
    expected_investigation_path: list[str] = Field(
        min_length=1,
        description="标准排查路径（D-18 权威源）：golden 逐字复制，第 1 步须指向"
        " inject.sh 直接产生的首个可独立观测信号",
    )
    expected_remediation: str = Field(min_length=1)

    @model_validator(mode="after")
    def _path_steps_not_blank(self) -> ScenarioSpec:
        blanks = [i for i, s in enumerate(self.expected_investigation_path) if not s.strip()]
        if blanks:
            msg = f"expected_investigation_path 第 {blanks} 步为空白，排查路径步骤不得为空"
            raise ValueError(msg)
        return self


class AlertEvent(BaseModel):
    """黄金集里的一次告警触发/恢复记录。"""

    model_config = ConfigDict(extra="forbid")

    alert_name: str = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)
    fired_at: datetime
    resolved_at: datetime | None = None
    # D-21（issue 06）：可选标注 incident/false_positive，缺省 incident 向后兼容既有 11 剧本
    classification: Literal["incident", "false_positive"] = "incident"

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
    """黄金集与剧本的对应关系校验（D-18：三标注字段逐字一致，防标注漂移）。
    P2 教训：investigation_path 曾无权威源致跨剧本复制无人拦截，现三字段必须与 expected_* 逐字相同。
    """
    return (
        golden.scenario == spec.name
        and golden.root_cause == spec.expected_root_cause
        and golden.investigation_path == spec.expected_investigation_path
        and golden.remediation == spec.expected_remediation
    )


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

# D-21 过渡豁免（issue 06）：holdout 同步延至 M7 前、M2 期间禁看不动。清单内 slug 允许
# dev 单边（跳过 R2 + R3-holdout/R4，R1/R3-dev/R6 照跑）；显式常量而非放宽 R2 为警告
# ——M7 同步 holdout 后清空本清单，成对硬约束原样恢复（M7 待办）。
HOLDOUT_SYNC_PENDING: frozenset[str] = frozenset({"false-positive-flap"})


def _cross_check_slug(slug: str, tree: dict[str, dict[str, GoldenSet]], spec: ScenarioSpec) -> None:
    """R6：timeline ⊆ expected_alerts + 三标注逐字一致（D-18）。
    只校验实际存在的 split（D-21 过渡豁免路径下 holdout 可能缺席）。
    """
    timeline_alerts = {
        a.alert_name
        for split in _SPLITS
        if slug in tree[split]
        for r in tree[split][slug].runs
        for a in r.alert_timeline
    }
    unobserved = timeline_alerts - set(spec.expected_alerts)
    if unobserved:
        msg = (
            f"{slug}: 时间线出现 expected_alerts 之外的告警 {sorted(unobserved)}"
            "——预标注声称的告警必须实测触发过（P1 教训：推演级联不得写入预期）"
        )
        raise ScenarioValidationError(msg)
    if not golden_matches_scenario(tree["dev"][slug], spec):
        msg = (
            f"{slug}: dev 集标注与 scenario.yaml expected_* 不逐字一致"
            "（root_cause/investigation_path/remediation 三字段同源纪律）"
        )
        raise ScenarioValidationError(msg)
    if slug in tree["holdout"] and not golden_matches_scenario(tree["holdout"][slug], spec):
        msg = f"{slug}: holdout 集标注与 scenario.yaml expected_* 不逐字一致（双集标注必须同步）"
        raise ScenarioValidationError(msg)


def _load_specs(scenarios_dir: Path) -> dict[str, ScenarioSpec]:
    """加载剧本目录下全部 ScenarioSpec（R6 前置；目录缺失即报错）。"""
    if not scenarios_dir.is_dir():
        msg = f"scenarios_dir 不存在: {scenarios_dir}——R6 交叉校验需要剧本目录"
        raise ScenarioValidationError(msg)
    specs: dict[str, ScenarioSpec] = {}
    for p in sorted(scenarios_dir.iterdir()):
        if (p / "scenario.yaml").is_file():
            spec = load_scenario_file(p / "scenario.yaml")
            specs[spec.name] = spec
    return specs


def _check_dev_min_runs(slug: str, dev: GoldenSet) -> None:
    """R3-dev 单侧检查（过渡豁免路径：holdout 尚未同步时仍强制 dev ≥2）。"""
    if len(dev.runs) < DEV_MIN_RUNS:
        msg = f"{slug}: dev 集仅 {len(dev.runs)} run，下限 {DEV_MIN_RUNS}（口径 dev2+holdout1）"
        raise ScenarioValidationError(msg)


def _check_pair_rules(slug: str, dev: GoldenSet, holdout: GoldenSet) -> None:
    """R2 成对 / R3 run 数下限 / R4 防复制（dev+holdout 双集结构规则）。"""
    _check_dev_min_runs(slug, dev)
    dev_runs, holdout_runs = dev.runs, holdout.runs
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


def load_golden_tree(
    root: str | Path, scenarios_dir: str | Path | None = None
) -> dict[str, dict[str, GoldenSet]]:
    """扫描 datasets/golden/{dev,holdout}/<slug>.yaml 双集目录树并校验（issue 05 / D-18）。

    双集隔离（architecture.md §6）：dev 调参可看，holdout 终评专用。
    目录树层规则（schema 层只管单文件）：
    R1 文件名（去扩展名）必须等于 golden.scenario 字段
    R2 成对：slug 必须同时出现在 dev 与 holdout
      （D-21 豁免：HOLDOUT_SYNC_PENDING 内 slug 允许 dev 单边，M7 同步后清空恢复）
    R3 run 数下限：dev ≥2，holdout ≥1（每剧本 ×3 = 2+1 的拆分口径）
    R4 防复制：同一 slug 在 dev 与 holdout 不得出现相同 started_at 的 run
    R5 空树允许（分批采集中），已存在的文件必须合法
    R6 交叉校验（仅当传入 scenarios_dir）：timeline alertname ⊆ expected_alerts
       （P1 防线：预标注声称的告警必须在实测时间线出现过）+ 三标注字段与
       scenario.yaml 逐字一致（P2 防线）；golden 引用不存在的剧本同样报错。

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

    # R6 前置：加载剧本 spec（slug 无对应剧本在交叉校验时暴露）
    specs = _load_specs(scenarios_dir) if scenarios_dir is not None else {}

    slugs = {s for split in _SPLITS for s in tree[split]}
    for slug in sorted(slugs):
        in_dev, in_holdout = slug in tree["dev"], slug in tree["holdout"]
        if in_dev != in_holdout:  # R2
            if in_dev and slug in HOLDOUT_SYNC_PENDING:
                pass  # D-21 过渡豁免：holdout 同步延至 M7 前，dev 单边放行
            else:
                missing = "holdout" if in_dev else "dev"
                msg = f"{slug}: 黄金集双集不成对——{missing} 目录缺少 {slug}.yaml"
                raise ScenarioValidationError(msg)

        if in_dev and in_holdout:
            _check_pair_rules(slug, tree["dev"][slug], tree["holdout"][slug])
        elif in_dev:
            # 过渡豁免路径（或仅 dev 存在的豁免清单 slug）：R3-dev 照跑，
            # R3-holdout / R4 无 holdout 可比，随 M7 同步恢复
            _check_dev_min_runs(slug, tree["dev"][slug])

        if scenarios_dir is not None:  # R6
            spec = specs.get(slug)
            if spec is None:
                msg = f"{slug}: 黄金集引用了不存在的剧本（{scenarios_dir} 下无 name={slug}）"
                raise ScenarioValidationError(msg)
            _cross_check_slug(slug, tree, spec)
    return tree
