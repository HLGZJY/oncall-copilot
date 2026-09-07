"""few-shot 样本池 loader（issue 03 / G3 ② 定案 + G9 holdout 禁看纪律）。

只读 `datasets/golden/dev/`，按 golden timeline 条目的**可选** `classification`
字段分组（缺省 `incident`，D-21），每态取 K 条；**排除 3 验证剧本**
（slow-sql / protocol-mismatch / false-positive-flap，防自证泄漏）。

泄漏守卫三重闸：
1. 排除名单硬编码 3 验证剧本——06 落地 false-positive-flap 后自动仍被排除；
2. 目录名硬守卫：传入目录 resolved 后必须名为 `dev`，把 holdout 路径
   传进来在入口即 ValueError（fail-fast 优先于静默产出污染样本池）；
3. 禁止复用 schema 层的黄金树加载器——它会读 holdout split 做成对校验（R2），
   本模块源码不含该符号名（守卫测试断言）。

样本不写死剧本名单：false_positive 样本待 issue 06 落地后自动进入。
loader 只消费 golden 的 scenario / root_cause / timeline(labels/fired_at/
resolved_at/classification) 字段，不触碰 D-18 三字段同源校验（那是
golden_matches_scenario 的职责）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "DEFAULT_DEV_DIR",
    "DEFAULT_PER_CLASS_K",
    "VALIDATION_SCENARIO_SLUGS",
    "FewShotSample",
    "load_few_shot_samples",
]

#: 3 验证剧本（G3 ② / D-21）：这些剧本要用来做 M2 验收，绝不能进 few-shot
VALIDATION_SCENARIO_SLUGS = frozenset({"slow-sql", "protocol-mismatch", "false-positive-flap"})

#: 每态样本数（G3 ②：K=2–3；取下限 2 控 prompt token 预算）
DEFAULT_PER_CLASS_K = 2

#: D-21：timeline 条目 classification 可选字段缺省值
DEFAULT_CLASSIFICATION = "incident"

DEFAULT_DEV_DIR = Path("datasets/golden/dev")


@dataclass(frozen=True)
class FewShotSample:
    """单条 few-shot 样本：输入 = golden 同源紧凑卡片，输出 = 标注三态之一。"""

    scenario: str
    alert_card: dict[str, Any] = field(default_factory=dict)
    verdict: str = DEFAULT_CLASSIFICATION  # 仅 false_positive / incident（LLM 不直出 risk）
    reason: str = ""  # 来自 golden root_cause（同源纪律，不另行编造）


def load_few_shot_samples(
    dev_dir: str | Path = DEFAULT_DEV_DIR, *, per_class: int = DEFAULT_PER_CLASS_K
) -> list[FewShotSample]:
    """扫 dev 目录产出 few-shot 样本池（确定性：场景名字典序截取 K 条/态）。"""
    base = Path(dev_dir).resolve()
    if base.name != "dev":
        raise ValueError(
            f"few-shot loader 只允许读 dev 目录（双集隔离，holdout 禁看），收到：{base}"
        )

    pool: dict[str, list[FewShotSample]] = {}
    for path in sorted(base.glob("*.yaml")):
        slug = path.stem
        if slug in VALIDATION_SCENARIO_SLUGS:
            continue
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        sample = _sample_from_golden(slug, payload)
        if sample is not None:
            pool.setdefault(sample.verdict, []).append(sample)

    samples: list[FewShotSample] = []
    for verdict in sorted(pool):  # 态之间也按名字典序，保证整体确定性
        samples.extend(sorted(pool[verdict], key=lambda s: s.scenario)[:per_class])
    return samples


def _sample_from_golden(slug: str, payload: dict[str, Any]) -> FewShotSample | None:
    """从单份 golden yaml 取首个 timeline 条目构造样本（1 剧本 1 样本）。"""
    runs = payload.get("runs") or []
    for run in runs:
        for entry in run.get("alert_timeline") or []:
            return FewShotSample(
                scenario=slug,
                alert_card={
                    "alert": {
                        "labels": entry.get("labels") or {"alertname": entry.get("alert_name")},
                        "fired_at": entry.get("fired_at"),
                        "resolved_at": entry.get("resolved_at"),
                    }
                },
                verdict=entry.get("classification", DEFAULT_CLASSIFICATION),
                reason=payload.get("root_cause", ""),
            )
    return None
