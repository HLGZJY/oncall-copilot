"""make eval / make eval-real 一键入口（m7 issue 06 / G1 回归面）。

编排复用 issue 05 `run_matrix` + `write_report`（勿重造）与 issue 03
`rule_judge`（判定接缝），组件装配全 mock（MockPlanner + MockVerifierJudge，
零真实 API 调用——mock 档 CI 秒级回归的前提，G1）。

- `run_mock_eval`：2 profile × 12 dev 剧本 × N=3 矩阵，双产物落 `datasets/eval/`；
- `run_eval_real`：只做 `ONCALL_RUN_M7_EVAL=1` 门槛校验（`require_real_tier`
  复用，D-58），**不真跑**——真实档跑批与档位回填归 issue 07，落地前显式
  deferred 信号（静默 mock 会伪装成真评测，0 漏报纪律）。

CLI：`python -m oncall.eval.entry [mock|real]`（Makefile `eval` / `eval-real`
与 CI eval-regression job 同源调用此入口，防 shell 层与 Python 层分叉）。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.eval.golden import DEFAULT_GOLDEN_ROOT, GoldenScenario, load_golden_dir
from oncall.eval.judging import rule_judge
from oncall.eval.report import EVAL_DIR, ModelProfile, run_matrix, write_report
from oncall.eval.runner import (
    N_RUNS,
    REAL_TIER_ENV,
    ScenarioCase,
    ScenarioRunSpec,
    require_real_tier,
)
from oncall.harness.loop import LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner
from oncall.harness.tools.registry import ToolRegistry
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict

__all__ = [
    "MOCK_PROFILE_NAMES",
    "REAL_TIER_DEFERRED_MSG",
    "main",
    "run_eval_real",
    "run_mock_eval",
]

#: mock 档矩阵 profile 名（G8：档位标签非模型名，具体模型档位随 issue 07 回填）
MOCK_PROFILE_NAMES = ("mock-a", "mock-b")

#: 真实档 deferred 信号文案（issue 07 落地前入口不真跑）
REAL_TIER_DEFERRED_MSG = "真实档跑批归 issue 07"


def _mock_factory(golden: GoldenScenario, run_idx: int, profile: ModelProfile) -> LoopComponents:
    """mock 档装配（与 test_eval_report 同构）：MockPlanner 直接收束，零工具步。"""
    del profile, run_idx  # mock 档 profile/run_idx 只决定矩阵行键，装配面无差异
    return LoopComponents(
        planner=MockPlanner(conclusion=golden.root_cause),
        registry=ToolRegistry(),
        gate=PermissionGate(),
        verifier=Verifier(
            judge=MockVerifierJudge(default=VerifierVerdict(supported=True, reason="mock 裁决"))
        ),
        now=lambda: datetime.now(UTC),
    )


def _golden_case(profile: ModelProfile, golden: GoldenScenario) -> ScenarioCase:
    """单格 case：golden 标注 + 行键（scenario/the_set=dev/model=profile 名）成对。"""
    return ScenarioCase(
        golden=golden,
        spec=ScenarioRunSpec(scenario=golden.scenario, the_set="dev", model=profile.name),
    )


def _matrix_session(db_url: str) -> Session:
    """入口 DB 会话：`ONCALL_DATABASE_URL` 同词汇（ingest/app.py 同源默认）。"""
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
    )
    create_tables(engine)
    return Session(engine)


def run_mock_eval(
    *,
    db_session: Session | None = None,
    golden_root: Path | str = DEFAULT_GOLDEN_ROOT,
    out_dir: Path | str = EVAL_DIR,
    n_runs: int = N_RUNS,
) -> tuple[list, Path, Path]:
    """mock 档一键评测（make eval / CI 同源）：矩阵实跑 + 双产物落盘。

    返回 `(eval_runs 明细行, json 路径, markdown 路径)`；表是明细权威（D-62），
    导出是序列化副本（G5）。默认落仓库 dev DB（`ONCALL_DATABASE_URL` 可换）。
    外部传入 `db_session` 时只 flush 不 commit（事务归调用方）。
    """
    goldens = load_golden_dir(Path(golden_root) / "dev", the_set="dev")
    profiles = [ModelProfile(name=name) for name in MOCK_PROFILE_NAMES]
    cases = [_golden_case(profile, golden) for profile in profiles for golden in goldens]
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
            components_factory=_mock_factory,
            judger=rule_judge,
            db_session=session,
            n_runs=n_runs,
        )
        session.flush()
        json_path, md_path = write_report(rows, out_dir)
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


def run_eval_real() -> None:
    """真实档入口门槛校验：未解锁 raise；解锁后 deferred（issue 07 落地前不真跑）。"""
    require_real_tier()
    raise NotImplementedError(
        f"{REAL_TIER_DEFERRED_MSG}：入口与 {REAL_TIER_ENV}=1 门槛已收口（本票），"
        "真实档组件装配/跑批/档位回填随 issue 07 落地"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI：`python -m oncall.eval.entry [mock|real] [--out-dir DIR]`，默认 mock。"""
    args = list(sys.argv[1:] if argv is None else argv)
    out_dir: Path = EVAL_DIR
    if "--out-dir" in args:
        idx = args.index("--out-dir")
        out_dir = Path(args.pop(idx + 1))
        args.pop(idx)
    tier = args[0] if args and args[0] in ("mock", "real") else "mock"
    if tier == "real":
        if os.environ.get(REAL_TIER_ENV) != "1":
            sys.stderr.write(
                f"==> 门槛未解锁：须显式设 {REAL_TIER_ENV}=1 才允许真实档"
                "（require_real_tier 同口径）\n"
            )
            return 1
        try:
            run_eval_real()
        except NotImplementedError:
            sys.stderr.write(f"==> {REAL_TIER_DEFERRED_MSG}（门槛已通过）\n")
            return 1  # deferred：真实档跑批未落地，非零退出防 CI 假绿
        return 0
    _, json_path, md_path = run_mock_eval(out_dir=out_dir)
    sys.stdout.write(f"==> mock 档矩阵完成：{json_path}\n")
    sys.stdout.write(f"==> 人读报告：{md_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
