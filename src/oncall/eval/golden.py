"""黄金集加载器 + 三条防泄漏守卫（m7 issue 01 / D-59 + R6）。

防泄漏纪律（M2 issue 03/05 先例，设计文档 G2/R6）：
- 守卫①：holdout 未显式解锁（`ONCALL_M7_HOLDOUT_UNLOCK=1`）加载即 fail
  ——保留集只做最终评测，开发期禁读；
- 守卫②：few-shot 源禁含 3 验证剧本（复用 `classify.llm.fewshot`
  的冻结名单 `VALIDATION_SCENARIO_SLUGS`，勿重造）；
- 守卫③：标注完整性——缺 `scenario` / `root_cause` 即 fail（无标注不可
  评，禁虚构口径），scenario slug 重复即 fail（评测矩阵行不可歧义）。

加载结果为 pydantic `GoldenScenario`（契约模型，与 classify 契约同风格）。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from oncall.classify.llm.fewshot import VALIDATION_SCENARIO_SLUGS

__all__ = [
    "DEFAULT_GOLDEN_ROOT",
    "FewShotLeakError",
    "GoldenAnnotationError",
    "GoldenScenario",
    "HoldoutLockedError",
    "filter_fewshot_scenarios",
    "load_golden_dir",
]

#: 黄金集根目录（dev/ 与 holdout/ 双集，防泄漏隔离）
DEFAULT_GOLDEN_ROOT = Path("datasets/golden")

#: 守卫①解锁 env：只有显式 `=1` 才允许加载 holdout（最终评测通道）
HOLDOUT_UNLOCK_ENV = "ONCALL_M7_HOLDOUT_UNLOCK"


class HoldoutLockedError(RuntimeError):
    """守卫①：holdout 未显式解锁（开发期禁读保留集）。"""


class FewShotLeakError(RuntimeError):
    """守卫②：few-shot 源混入验证剧本（验收集泄漏进提示词）。"""


class GoldenAnnotationError(RuntimeError):
    """守卫③：标注不完整或 slug 重复（无标注不可评）。"""


class GoldenScenario(BaseModel):
    """黄金剧本标注（预标注正确根因与期望处置，CONTEXT.md「黄金集」口径）。"""

    scenario: str
    root_cause: str
    remediation: str
    investigation_path: list[str] = Field(default_factory=list)
    runs: list[dict[str, object]] = Field(default_factory=list)

    @staticmethod
    def assert_fewshot_clean(slugs: Iterable[str]) -> None:
        """守卫②断言面：候选 few-shot 源混入验证剧本即 raise（泄漏零容忍）。"""
        leaked = sorted(set(slugs) & VALIDATION_SCENARIO_SLUGS)
        if leaked:
            raise FewShotLeakError(f"few-shot 源含验证剧本（禁入提示词，M2 G3 ②/D-21）：{leaked}")


def filter_fewshot_scenarios(
    scenarios: list[GoldenScenario],
) -> tuple[list[GoldenScenario], list[GoldenScenario]]:
    """守卫②过滤面：按冻结名单切分（保留, 排除）——few-shot 组装只能消费保留侧。"""
    keep = [s for s in scenarios if s.scenario not in VALIDATION_SCENARIO_SLUGS]
    excluded = [s for s in scenarios if s.scenario in VALIDATION_SCENARIO_SLUGS]
    return keep, excluded


def load_golden_dir(directory: Path | str, *, the_set: str) -> list[GoldenScenario]:
    """加载一个黄金集目录，三守卫全开。

    `the_set` 只接受 `dev` / `holdout`（与 eval_runs.the_set CHECK 同词汇）；
    holdout 须显式解锁。守卫③逐文件校验 + 全集 slug 去重。
    """
    if the_set not in ("dev", "holdout"):
        raise ValueError(f"the_set 只接受 dev|holdout，得到 {the_set!r}")
    if the_set == "holdout" and os.environ.get(HOLDOUT_UNLOCK_ENV) != "1":
        raise HoldoutLockedError(
            f"holdout 保留集锁定：只做最终评测，开发期禁读（防泄漏守卫①）；"
            f"最终评测显式设 {HOLDOUT_UNLOCK_ENV}=1 解锁"
        )

    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"黄金集目录不存在：{root}")

    scenarios: list[GoldenScenario] = []
    seen: set[str] = set()
    for path in sorted(root.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise GoldenAnnotationError(f"{path.name}：顶层结构须为映射，得到 {type(raw).__name__}")
        # 守卫③：无标注不可评（缺关键字段即 fail，禁虚构口径）
        missing = [k for k in ("scenario", "root_cause", "remediation") if not raw.get(k)]
        if missing:
            raise GoldenAnnotationError(f"{path.name}：缺标注字段 {missing}（无标注不可评）")
        scenario = GoldenScenario.model_validate(raw)
        if scenario.scenario in seen:
            raise GoldenAnnotationError(
                f"{path.name}：scenario {scenario.scenario!r} 重复（矩阵行不可歧义）"
            )
        seen.add(scenario.scenario)
        scenarios.append(scenario)
    return scenarios
