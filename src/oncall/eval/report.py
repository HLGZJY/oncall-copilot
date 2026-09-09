"""矩阵跑法 + 模型 profile 参数化 + 报告双产物（m7 issue 05）。

设计口径（docs/design/m7-eval-bench-design.md）：
- G4：每剧本每模型 N=3，消费 issue 04 `summarize_runs`（均值±极差；
  同组判定不一致 `unstable` 单列不静默平均，D-61）；
- G5：双产物落 `datasets/eval/`——`runs-<date>.json`（机器可读，携带
  eval_runs 行 id 供 SQL 抽验回溯）+ `report-<date>.md`（人读矩阵表 +
  选型理由骨架）；表是明细权威，导出是序列化副本（D-49）；
- G8：模型 profile 参数化——每个 profile 一个 env 前缀
  `ONCALL_LLM_PROFILE_<NAME>_*`，装配复用 `LLMClientConfig.from_env`
  既有切换面，**零硬编码模型名**（具体档位随 key 门槛票实测回填）。

矩阵行全部来自 `run_scenario` 实跑产出（硬规 10 禁虚构）；本模块只做
编排（profile × 剧本 × N 遍）与序列化，不重造指标核算。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from oncall.db.eval_models import EvalRun
from oncall.eval.golden import GoldenScenario
from oncall.eval.metrics import summarize_runs
from oncall.eval.runner import N_RUNS, ScenarioCase, run_scenario
from oncall.harness.loop import LoopComponents
from oncall.infra.llm import LLMClientConfig

#: 报告产物落点（G5：与黄金集同域，进 git）
EVAL_DIR = Path("datasets/eval")

#: G8 profile env 尾词：`ONCALL_LLM_PROFILE_<NAME>_<尾词>`，尾词与既有
#: `ONCALL_LLM_<尾词>` 切换面同词汇（BASE_URL/MODEL/API_KEY/TIMEOUT_SECONDS）
PROFILE_PREFIX_FMT = "ONCALL_LLM_PROFILE_{name}_"
PROFILE_ENV_SUFFIXES = ("BASE_URL", "MODEL", "API_KEY", "TIMEOUT_SECONDS")

#: 矩阵组件装配面：(golden, run_idx, profile) → LoopComponents（mock/真实由调用方定档）
MatrixComponentsFactory = Callable[[GoldenScenario, int, "ModelProfile"], LoopComponents]


@dataclass(frozen=True)
class ModelProfile:
    """模型档案（G8/D-65）：只有名字与 env 前缀，零硬编码模型名。"""

    name: str

    @property
    def env_prefix(self) -> str:
        return PROFILE_PREFIX_FMT.format(name=self.name.upper())


def resolve_profile(
    profile: ModelProfile, *, env: Mapping[str, str] | None = None
) -> LLMClientConfig:
    """按 profile env 前缀装配真实 client 配置（复用 from_env，缺项 fail-fast）。

    即把 `ONCALL_LLM_PROFILE_<NAME>_*` 映射回既有 `ONCALL_LLM_*` 词汇后
    交给 `LLMClientConfig.from_env`——切换面单源，不改 llm.py 冻结面。
    NAME 中的连字符与下划线等价（`K2-6` ≡ `K2_6`，env 命名惯例优先下划线）。
    """
    environ = os.environ if env is None else env

    def _lookup(tail: str) -> str | None:
        for prefix in (profile.env_prefix, profile.env_prefix.replace("-", "_")):
            value = environ.get(prefix + tail)
            if value is not None:
                return value
        return None

    mapped = {
        "ONCALL_LLM_" + tail: value
        for tail in PROFILE_ENV_SUFFIXES
        if (value := _lookup(tail)) is not None
    }
    return LLMClientConfig.from_env(mapped)


def run_matrix(  # noqa: PLR0913 —— 矩阵编排面 = runner 五参 + profiles，拆对象反而藏依赖
    *,
    cases: Sequence[ScenarioCase],
    profiles: Sequence[ModelProfile],
    components_factory: MatrixComponentsFactory,
    judger: Callable[[GoldenScenario, Any], Any],
    db_session: Session,
    n_runs: int = N_RUNS,
) -> list[EvalRun]:
    """G4 矩阵编排：profile × 剧本 × N 遍，每格委托 `run_scenario` 实跑。

    行键（scenario/the_set/model）由 `ScenarioCase.spec` 携带——调用方为
    每个 profile 生成对应 case（model=profile.name），保证行与 profile 对位。
    """
    rows: list[EvalRun] = []
    profile_names = {profile.name for profile in profiles}
    orphan = [case.spec.model for case in cases if case.spec.model not in profile_names]
    if orphan:
        raise ValueError(f"case.spec.model 无对应 profile: {sorted(set(orphan))}")
    for profile in profiles:
        for case in cases:
            if case.spec.model != profile.name:
                continue  # 该剧本不属于本 profile（对位过滤）
            rows.extend(
                run_scenario(
                    case=case,
                    components_factory=lambda golden, run_idx, _p=profile: components_factory(
                        golden, run_idx, _p
                    ),
                    judger=judger,
                    db_session=db_session,
                    n_runs=n_runs,
                )
            )
    db_session.flush()  # 落 id：报告 run_ids 可回溯的前提
    return rows


def build_matrix_report(rows: Sequence[EvalRun], *, generated_at: datetime) -> dict[str, Any]:
    """eval_runs 行集合 → 报告结构（G4 消费 `summarize_runs`；G5 可回溯）。

    每组携带 `run_ids`（eval_runs 行 id 升序）——报告每个数字可 SQL 抽验
    回溯到表行（硬规 10）；本函数纯组装不核算，数字全部来自 issue 04 口径。
    """
    by_key: dict[tuple[str, str, int], int] = {}
    for row in rows:
        by_key[(row.scenario, row.model, row.run_idx)] = row.id

    groups: list[dict[str, Any]] = []
    for summary in summarize_runs(rows):
        key = (summary["scenario"], summary["model"])
        run_ids = [by_key[(key[0], key[1], i)] for i in range(summary["n_runs"])]
        groups.append({"run_ids": run_ids, **summary})

    return {
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "source": "eval_runs",
        "profiles": sorted({row.model for row in rows}),
        "groups": groups,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    """报告结构 → 人读汇总 markdown（G5）：矩阵表 + 选型理由骨架。"""
    lines = [
        "# M7 评测矩阵报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 数据来源：`{report['source']}` 表（每组含 run_ids，可 SQL 抽验回溯）",
        f"- 模型档案：{', '.join(report['profiles'])}",
        "",
        "## 矩阵表（每剧本每模型 N=3，均值±极差）",
        "",
        "| 剧本 | 模型 | 集合 | 遍数 | Top-1 | Top-3 | 步数(均值±极差) | 成本¥(均值±极差) "
        "| unstable | 失败模式 | escalated |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for g in report["groups"]:
        top1 = f"{g['top1_hits']}/{g['eligible']}" if g["rates_defined"] else "-"
        top3 = f"{g['top3_hits']}/{g['eligible']}" if g["rates_defined"] else "-"
        steps = _avg_range(g["steps_avg"], g["steps_range"])
        cost = _avg_range(g["cost_avg"], g["cost_range"])
        lines.append(
            f"| {g['scenario']} | {g['model']} | {g['the_set']} | {g['n_runs']} "
            f"| {top1} | {top3} | {steps} | {cost} "
            f"| {'⚠️' if g['unstable'] else ''} | {g['failure_mode'] or '-'} "
            f"| {g['escalated_count']} |"
        )
    lines += [
        "",
        "## 选型理由（骨架）",
        "",
        "> 待真实档实测回填（G8：具体档位随 key 门槛票拍板，禁虚构结论）。",
        "",
        "- **质量**：Top-1/Top-3 命中对比（unstable 行需人工复核后采信）",
        "- **成本**：单次调查成本均值（估算列，与 usage_log 实测分列不混算）",
        "- **延迟**：单次调查耗时均值",
        "- **结论**：待回填",
        "",
    ]
    return "\n".join(lines)


def _avg_range(avg: float | None, rng: float | None) -> str:
    if avg is None or rng is None:
        return "-"
    return f"{avg:.2f}±{rng:.2f}"


def write_report(
    rows: Sequence[EvalRun],
    out_dir: Path | str = EVAL_DIR,
    *,
    generated_at: datetime | None = None,
) -> tuple[Path, Path]:
    """G5 双产物落盘：`runs-<YYYYMMDD>.json` + `report-<YYYYMMDD>.md`。"""
    stamp = (generated_at or datetime.now(UTC)).strftime("%Y%m%d")
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    report = build_matrix_report(rows, generated_at=generated_at or datetime.now(UTC))

    json_path = directory / f"runs-{stamp}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = directory / f"report-{stamp}.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
