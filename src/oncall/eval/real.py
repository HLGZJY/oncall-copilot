"""真实档跑批（m7 issue 07）：`run_eval_real` 装配面 + `.env` 落位 + usage 实测。

拆分自 entry.py（C6 ≤300）：entry 保留 mock 档与 CLI 编排，真实档独立成模块。

- 门槛：`ONCALL_RUN_M7_EVAL=1`（`require_real_tier`，D-58）；mock 档 CI 不受影响；
- 装配：`resolve_profile` 按 `ONCALL_LLM_PROFILE_<NAME>_*` env 装配真实 planner；
  golden 证据面（D-18，不含 root_cause）；裁决接缝留 mock（真判官是事后两级判定）；
- 判定：`two_tier_judger`（规则全量兜底 → 规则未命中交真实 judge，
  `ONCALL_JUDGE_LLM_*` 防自评）；judge 异常回退规则 miss 留追溯痕；
- usage 实测（硬规 10）：每行 `run_json.usage_real` = planner usage_log 实测
  tokens × 单价 env（`*_PRICE_IN/PRICE_OUT`，¥/M tokens）——G3 与估算列分列
  不混算；代码零硬编码模型名与牌价（G8）；
- `.env` 落位（D-67，key 不落库不入 git）：`load_env_file` 只补缺不覆盖
  （显式 env 优先）；路径 `ONCALL_M7_ENV_FILE` 可换，默认仓库根 `.env`。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.eval.evidence import make_golden_handlers
from oncall.eval.golden import DEFAULT_GOLDEN_ROOT, GoldenScenario, load_golden_dir
from oncall.eval.judging import RealJudge, resolve_judge_config, two_tier_judger
from oncall.eval.report import (
    EVAL_DIR,
    PROFILE_PREFIX_FMT,
    ModelProfile,
    resolve_profile,
    run_matrix,
    write_report,
)
from oncall.eval.runner import (
    N_RUNS,
    ScenarioCase,
    ScenarioRunSpec,
    require_real_tier,
)
from oncall.harness.loop import LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict
from oncall.infra.llm import LLMUsage
from oncall.infra.llm_judge import OpenAIJudgeClient
from oncall.infra.llm_planner import OpenAIPlannerClient

__all__ = [
    "ENV_FILE_ENV",
    "PRICE_SUFFIXES",
    "REAL_PROFILE_NAMES",
    "load_env_file",
    "run_eval_real",
]

#: 真实档矩阵 profile 名（D-66 拍板：kimi-k2.6 质量上探 vs kimi-k2.5 性价比基线；
#: 模型名/单价只活在 env 与报告回填，代码零硬编码——G8）
REAL_PROFILE_NAMES = ("k2-6", "k3")

#: .env 路径 env（D-67：默认仓库根 .env，key 不落库不入 git）
ENV_FILE_ENV = "ONCALL_M7_ENV_FILE"

#: profile/judge 单价 env 尾词（¥/M tokens，牌价 env 化随平台更新）
PRICE_SUFFIXES = ("PRICE_IN", "PRICE_OUT")


# ── .env 落位（D-67）─────────────────────────────────────────────────


def load_env_file(path: Path | str | None = None) -> dict[str, str]:
    """读 `.env`（KEY=VALUE，容忍引号），只补 os.environ 缺项不覆盖显式 env。

    返回解析出的键值对（含未应用的键）；注释/空行/无 `=` 畸形行跳过。
    """
    env_path = Path(
        path or os.environ.get(ENV_FILE_ENV) or Path(__file__).resolve().parents[3] / ".env"
    )
    parsed: dict[str, str] = {}
    if not env_path.is_file():
        return parsed
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        parsed[key] = value
        os.environ.setdefault(key, value)
    return parsed


# ── 真实档装配 ───────────────────────────────────────────────────────


def _real_factory(
    usage_map: dict[tuple[str, str, int], list[LLMUsage]],
    golden: GoldenScenario,
    run_idx: int,
    profile: ModelProfile,
) -> LoopComponents:
    """真实档装配：env profile → OpenAIPlannerClient + golden 证据面（D-18）。

    裁决接缝留 mock supported（真判官是事后 `two_tier_judger`，不在循环内——
    M3-08 先例：判分范围仅 Planner）；planner usage_log 按行键留存供回填。
    """

    def now() -> datetime:
        return datetime.now(UTC)

    registry = ToolRegistry(now=now)
    register_six_tools(registry, make_golden_handlers(golden))
    planner = OpenAIPlannerClient(resolve_profile(profile))
    usage_map[(profile.name, golden.scenario, run_idx)] = planner.usage_log
    return LoopComponents(
        planner=planner,
        registry=registry,
        gate=PermissionGate(now=now),
        verifier=Verifier(
            judge=MockVerifierJudge(
                default=VerifierVerdict(
                    supported=True, reason="证据支持（裁决接缝留 mock，M7 事后判分）"
                )
            )
        ),
        now=now,
    )


def _price_of(prefix: str, tail: str) -> float:
    """单价 env（¥/M tokens）→ 每token单价；缺省 0（成本列显式为 0 可追溯）。"""
    raw = os.environ.get(f"{prefix}{tail}", "").strip()
    try:
        return float(raw) / 1_000_000 if raw else 0.0
    except ValueError:
        return 0.0


def _usage_real_of(prefix: str, usages: list[LLMUsage]) -> dict[str, Any]:
    """usage 实测聚合：tokens 实测 × 单价 env（与 G8 估算列分列不混算）。"""
    price_in, price_out = _price_of(prefix, "PRICE_IN"), _price_of(prefix, "PRICE_OUT")
    prompt = sum(u.prompt_tokens for u in usages)
    completion = sum(u.completion_tokens for u in usages)
    return {
        "calls": len(usages),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": sum(u.total_tokens for u in usages),
        "latency_seconds": round(sum(u.latency_seconds for u in usages), 3),
        "cost_cny": round(prompt * price_in + completion * price_out, 6),
        "price_env": {"PRICE_IN": price_in * 1_000_000, "PRICE_OUT": price_out * 1_000_000},
    }


def _matrix_session(db_url: str) -> Session:
    """入口 DB 会话（与 entry 同词汇）：`ONCALL_DATABASE_URL` 可换。"""
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
    )
    create_tables(engine)
    return Session(engine)


def run_eval_real(  # noqa: PLR0913 —— 真实档装配面 = 矩阵五参 + 注入面，拆对象反而藏依赖
    *,
    db_session: Session | None = None,
    golden_root: Path | str = DEFAULT_GOLDEN_ROOT,
    out_dir: Path | str = EVAL_DIR,
    n_runs: int = N_RUNS,
    profile_names: Sequence[str] = REAL_PROFILE_NAMES,
    the_set: str = "dev",
    scenario_slugs: Sequence[str] | None = None,
    components_factory: Any | None = None,
    judge: Any | None = None,
) -> tuple[list, Path, Path]:
    """真实档一键评测（make eval-real）：真实 planner 跑批 + 判定 + 双产物落盘。

    门槛：`ONCALL_RUN_M7_EVAL=1`（require_real_tier）；judge env 缺项构造期
    fail-fast（LLMConfigError，禁静默回退）。注入面（测试替身/冒烟）：
    `components_factory` / `judge` / `scenario_slugs` / `n_runs` / `the_set`
    （holdout 须显式 `ONCALL_M7_HOLDOUT_UNLOCK=1`，守卫①同源）。
    """
    require_real_tier()
    load_env_file()
    judge = judge or RealJudge(OpenAIJudgeClient(resolve_judge_config()))
    goldens = [
        g
        for g in load_golden_dir(Path(golden_root) / the_set, the_set=the_set)
        if scenario_slugs is None or g.scenario in set(scenario_slugs)
    ]
    if not goldens:
        raise RuntimeError(f"评测集为空（golden_root={golden_root} the_set={the_set}）")
    profiles = [ModelProfile(name=name) for name in profile_names]
    cases = [
        ScenarioCase(
            golden=g,
            spec=ScenarioRunSpec(scenario=g.scenario, the_set=the_set, model=p.name),
        )
        for p in profiles
        for g in goldens
    ]
    usage_map: dict[tuple[str, str, int], list[LLMUsage]] = {}
    factory = components_factory or (lambda g, i, p: _real_factory(usage_map, g, i, p))
    owns_session = db_session is None
    session = (
        db_session
        if db_session is not None
        else _matrix_session(os.environ.get("ONCALL_DATABASE_URL", "sqlite:///./oncall.db"))
    )
    try:
        rows = run_matrix(
            cases=cases,
            profiles=profiles,
            components_factory=factory,
            judger=two_tier_judger(judge),
            db_session=session,
            n_runs=n_runs,
        )
        # judge client 全批共享，usage_log 是累计值——只在批级聚合一次
        # （逐行复制快照曾虚增 36 倍，2026-09-09 实测教训）
        judge_usage = getattr(getattr(judge, "_client", None), "usage_log", None) or []
        judge_summary = _usage_real_of("ONCALL_JUDGE_LLM_", judge_usage) if judge_usage else None
        for row in rows:
            # 连字符/下划线等价（与 report.resolve_profile 同规：K2-6 ≡ K2_6）
            prefix = PROFILE_PREFIX_FMT.format(name=row.model.upper()).replace("-", "_")
            usages = usage_map.get((row.model, row.scenario, row.run_idx), [])
            row.run_json = {
                **(row.run_json or {}),
                "usage_real": _usage_real_of(prefix, usages),
            }
        session.flush()
        json_path, md_path = write_report(rows, out_dir, judge_summary=judge_summary)
        if owns_session:
            session.commit()
        return rows, json_path, md_path
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()
