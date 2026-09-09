"""M7-07 真实档集成测试（issue 07）：ONCALL_RUN_M7_EVAL=1 显式解锁本地真跑。

真实面：OpenAIPlannerClient（profile env 装配）+ LLM-as-judge（ONCALL_JUDGE_LLM_*，
防自评）；取证面仍是 golden dev timeline 同源替身（D-18，不含 root_cause）。
CI 不进本文件（真实档跑批不污染 CI 面）；本地真跑方式::

    ONCALL_RUN_M7_EVAL=1 python -m pytest tests/integration/test_m7_real_eval.py -m integration

预算守门（G4 估算法）：本测试只跑单剧本 × 单 profile × 1 遍冒烟；全量跑批走
`make eval-real`（入口 `oncall.eval.entry real`）。实测 usage 回填
`.scratch/tmp/m7-07-real-eval-report.json`（禁虚构，硬规 10）。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.eval.entry import REAL_PROFILE_NAMES, run_eval_real

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ONCALL_RUN_M7_EVAL") != "1",
        reason="真实档跑批须显式 ONCALL_RUN_M7_EVAL=1（花钱开关；CI 不进）",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / ".scratch" / "tmp" / "m7-07-real-eval-report.json"

#: 冒烟口径：单剧本 × 2 profile × 1 遍（预算试跑定档；全量 72 次由入口跑批承担）
SMOKE_SCENARIO = os.environ.get("ONCALL_M7_EVAL_SMOKE_SCENARIO", "cpu-spike")


def _memory_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return Session(engine)


def test_real_tier_smoke_single_scenario() -> None:
    """真实档冒烟：真实 planner 跑通 1 剧本，usage 实测落记录，判定在契约词汇内。"""
    with _memory_session() as session:
        rows, json_path, md_path = run_eval_real(
            db_session=session,
            n_runs=1,
            profile_names=REAL_PROFILE_NAMES,
            scenario_slugs=[SMOKE_SCENARIO],
            out_dir=REPO_ROOT / ".scratch" / "tmp" / "m7-07-smoke",
        )
    assert rows, "真实档跑批零产出（装配面断裂）"
    assert {row.model for row in rows} == set(REAL_PROFILE_NAMES)
    assert {row.verdict for row in rows} <= {"top1", "top3", "miss"}
    for row in rows:
        usage = (row.run_json or {}).get("usage_real")
        assert usage is not None, "真实档行缺 usage_real（禁虚构，硬规 10）"
        assert usage["calls"] >= 1 and usage["total_tokens"] > 0
        assert usage["cost_cny"] > 0  # 实测 tokens × 单价（Kimi 牌价）
    record = {
        "segment": "m7-07-smoke",
        "generated_at": datetime.now(UTC).isoformat(),
        "scenario": SMOKE_SCENARIO,
        "rows": [
            {
                "model": row.model,
                "verdict": row.verdict,
                "judged_by": row.judged_by,
                "step_count": row.step_count,
                "duration_s": round(row.duration_s, 2),
                "conclusion": (row.run_json or {}).get("conclusion"),
                "usage_real": (row.run_json or {}).get("usage_real"),
            }
            for row in rows
        ],
        "report_paths": [str(json_path), str(md_path)],
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
