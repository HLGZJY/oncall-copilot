"""M3-08 真实 LLM 实测（issue 08 / T8-B）：3 剧本 OpenAIPlannerClient 端到端。

**花钱开关**：默认跳过，`ONCALL_RUN_LLM_E2E=1` 才执行（CI 不进默认门禁，
单独 schedule）。运行方式（先加载 key 三件套）::

    source ~/.oncall-llm-env && ONCALL_RUN_LLM_E2E=1 python -m pytest \
        tests/integration/test_planner_real_e2e.py -m integration

口径：
- 真实面只有 Planner（OpenAIPlannerClient 经 infra 收口，D-22）；取证面仍是
  golden timeline 同源 Fetcher 替身（D-18，不含 root_cause——被评对象不能看答案）；
- Verifier 裁决接缝留 mock（default=证实，判分范围仅 Planner；真判官归 M7）；
- 实测回填：步数 / 耗时 / 成本（usage × R9 单价，¥0.5 上限比对）/ 结论 +
  畸形率（风险 #1：>10% 触发 G1 复议——只记录上报，不改 G1 定案）；
- 记录落 `.scratch/tmp/m3-08-llm-e2e-report.json`，断言只守结构不守质量
  （结论质量按实测如实记录，判分口径复核归 M7）。
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

import golden_support
from oncall.api.investigation import InvestigationDeps
from oncall.db import AlertEvent, Incident, create_tables
from oncall.infra.llm_planner import OpenAIPlannerClient
from oncall.ingest.app import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ONCALL_RUN_LLM_E2E") != "1",
        reason="真实 LLM 实测须显式 ONCALL_RUN_LLM_E2E=1（花钱开关，单独 schedule）",
    ),
]

SCENARIOS = ["cpu-spike", "slow-sql", "queue-backlog"]
REPORT_PATH = Path(__file__).resolve().parents[2] / ".scratch" / "tmp" / "m3-08-llm-e2e-report.json"

#: R9 单价（M2 issue 07 实测口径，2026-09-08 百炼牌价）：元每千 tokens
PRICING_IN_CNY_PER_1K = 0.00024
PRICING_OUT_CNY_PER_1K = 0.00096
COST_LIMIT_CNY = 0.5  # 验收上限


def _cost_cny(planner: OpenAIPlannerClient) -> float:
    return round(
        sum(
            usage.prompt_tokens / 1000 * PRICING_IN_CNY_PER_1K
            + usage.completion_tokens / 1000 * PRICING_OUT_CNY_PER_1K
            for usage in planner.usage_log
        ),
        6,
    )


def _make_engine() -> object:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return engine


def _seed_incident(engine: object, scenario: str) -> int:
    entry = golden_support.load_golden(scenario)["runs"][0]["alert_timeline"][0]
    with Session(engine) as session:  # type: ignore[arg-type]
        row = AlertEvent(
            fingerprint=f"fp-{scenario}",
            source="alertmanager",
            labels_json=entry["labels"],
            annotations_json={},
            fired_at=datetime.fromisoformat(entry["fired_at"]),
            status="deduped",
        )
        session.add(row)
        session.flush()
        incident = Incident(alert_ids=[row.id], severity="warning", status="investigating")
        session.add(incident)
        session.commit()
        return incident.id


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_real_llm_investigation_e2e(scenario: str):
    """真实 Planner 端到端：步数/耗时/成本/结论/判分/畸形率如实回填。"""
    doc = golden_support.load_golden(scenario)
    engine = _make_engine()
    incident_id = _seed_incident(engine, scenario)
    planner = OpenAIPlannerClient.from_env()
    app = create_app(
        engine,
        investigation=InvestigationDeps(
            components=golden_support.make_real_components(planner, doc)
        ),
    )
    client = TestClient(app)

    started = time.perf_counter()
    resp = client.post("/investigate", json={"incident_id": incident_id})
    duration_seconds = round(time.perf_counter() - started, 3)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["termination"] in {"concluded", "escalated", "aborted"}
    assert body["step_count"] <= 15
    assert duration_seconds <= 300
    cost = _cost_cny(planner)
    assert cost <= COST_LIMIT_CNY, f"成本 {cost} 超 ¥{COST_LIMIT_CNY} 上限"

    verdict = golden_support.match_root_cause(body["conclusion"], scenario)
    record = {
        "segment": "real",
        "scenario": scenario,
        "model": planner.model,
        "termination": body["termination"],
        "step_count": body["step_count"],
        "duration_seconds": duration_seconds,
        "failure_mode": body["failure_mode"],
        "stop_reason": body["stop_reason"],
        "confidence": body["confidence"],
        "match": verdict,
        "conclusion": body["conclusion"],
        "cost_cny": cost,
        "calls": len(planner.usage_log),
        "total_tokens": sum(u.total_tokens for u in planner.usage_log),
        "usage_detail": [
            {
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens": u.total_tokens,
                "latency_seconds": u.latency_seconds,
            }
            for u in planner.usage_log
        ],
    }
    golden_support.append_report(REPORT_PATH, record)
    print(
        f"\n[{scenario}] termination={body['termination']} steps={body['step_count']} "
        f"duration={duration_seconds}s cost=¥{cost} match={verdict['hit']}\n"
        f"conclusion: {body['conclusion']}"
    )
