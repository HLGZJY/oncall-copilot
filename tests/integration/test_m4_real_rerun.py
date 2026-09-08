"""M4-08 真实实测重跑（issue 08 / T8）：两缺口修复后 3 剧本 OpenAIPlannerClient 端到端。

**花钱开关**：默认跳过，`ONCALL_RUN_LLM_E2E=1` 才执行（照 M3 issue 08 先例，
开工前需用户确认 key 与预算）。运行方式（先加载 key 三件套）::

    source ~/.oncall-llm-env && ONCALL_RUN_LLM_E2E=1 python -m pytest \
        tests/integration/test_m4_real_rerun.py

口径（照 `test_planner_real_e2e.py` M3 先例，D-18/D-22/D-28/D-29）：
- 真实面只有 Planner（OpenAIPlannerClient 经 infra 收口）；取证面仍是 golden
  timeline 同源 Fetcher 替身（不含 root_cause/investigation_path/remediation）；
  Verifier 裁决接缝留 mock（default=证实，判分范围仅 Planner，真判官归 M7）；
- **落库链路全程生效**（`POST /investigate` 内建 EvidenceRepository，D-33/D-34）：
  真实轮即「100% 落库」实战验证——evidence_steps/hypotheses/investigations
  三表行数与终态逐项断言 + opening_card 留存断言 + GET 报告出口 roundtrip；
- 测量替身：Planner/Registry 外层计数代理（畸形率 = PlannerOutputError 占比、
  参数级失败率 = ToolParamError/UnknownToolError 占比）——只观测不改行为，
  生产代码与既有测试零改动；
- 实测回填：步数/耗时/成本（usage × R9 单价，¥0.5 上限比对）/结论 + Top-1
  （D-29 规则匹配级，口径复核归 M7）+ 畸形率/参数级失败率对比 M3 基线
  （首版 0/3，来源 `.scratch/m3-investigation-loop/issues/08-e2e-validation.md`）；
- 记录落 `.scratch/tmp/m4-08-llm-e2e-report.json`（不进版本库），断言只守
  结构不守质量（结论质量按实测如实记录，禁虚构）。
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, select
from sqlalchemy.orm import Session

import golden_support
from oncall.api.investigation import InvestigationDeps
from oncall.db import AlertEvent, Incident, create_tables
from oncall.db.models import EvidenceStep as EvidenceStepRow
from oncall.db.models import Hypothesis as HypothesisRow
from oncall.db.models import Investigation
from oncall.harness.planner import PlannerOutputError, PlannerTimeoutError
from oncall.harness.tools.registry import ToolParamError, UnknownToolError
from oncall.infra.llm_planner import OpenAIPlannerClient
from oncall.ingest.app import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ONCALL_RUN_LLM_E2E") != "1",
        reason="真实 LLM 实测须显式 ONCALL_RUN_LLM_E2E=1（花钱开关，开工前需用户确认 key）",
    ),
]

SCENARIOS = ["cpu-spike", "slow-sql", "queue-backlog"]
REPORT_PATH = Path(__file__).resolve().parents[2] / ".scratch" / "tmp" / "m4-08-llm-e2e-report.json"

#: R9 单价（M2 issue 07 实测口径，2026-09-08 百炼牌价）：元每千 tokens
PRICING_IN_CNY_PER_1K = 0.00024
PRICING_OUT_CNY_PER_1K = 0.00096
COST_LIMIT_CNY = 0.5  # 验收上限


class _MeasuredPlanner:
    """Planner 计数代理：只观测（畸形/超时/决策计数），不改 decide 行为。"""

    def __init__(self, inner: OpenAIPlannerClient) -> None:
        self._inner = inner
        self.decisions = 0
        self.conclusions = 0
        self.output_errors = 0
        self.timeouts = 0

    @property
    def inner(self) -> OpenAIPlannerClient:
        return self._inner

    @property
    def model(self) -> str:
        return self._inner.model

    def decide(self, context_view: Any) -> Any:
        try:
            decision = self._inner.decide(context_view)
        except PlannerOutputError:
            self.output_errors += 1
            raise
        except PlannerTimeoutError:
            self.timeouts += 1
            raise
        self.decisions += 1
        if decision.conclusion is not None:
            self.conclusions += 1
        return decision


class _MeasuredRegistry:
    """Registry 计数代理：只观测（参数级失败计数），不改 level/execute 行为。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.executions = 0
        self.param_errors = 0
        self.unknown_tools = 0

    def level(self, tool: str) -> Any:
        return self._inner.level(tool)

    def execute(self, tool: str, args: dict[str, Any], *, step_no: int) -> Any:
        try:
            execution = self._inner.execute(tool, args, step_no=step_no)
        except UnknownToolError:
            self.unknown_tools += 1
            raise
        except ToolParamError:
            self.param_errors += 1
            raise
        self.executions += 1
        return execution


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


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_m4_real_llm_investigation_e2e(scenario: str):
    """真实 Planner + 落库链路全程生效：步数/耗时/成本/判分/畸形率/参数失败率回填。"""
    doc = golden_support.load_golden(scenario)
    engine = _make_engine()
    incident_id = _seed_incident(engine, scenario)
    measured_planner = _MeasuredPlanner(OpenAIPlannerClient.from_env())
    base_components = golden_support.make_real_components(measured_planner, doc)
    measured_registry = _MeasuredRegistry(base_components.registry)
    app = create_app(
        engine,
        investigation=InvestigationDeps(
            components=replace(base_components, registry=measured_registry)
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
    planner = measured_planner.inner
    cost = _cost_cny(planner)
    assert cost <= COST_LIMIT_CNY, f"成本 {cost} 超 ¥{COST_LIMIT_CNY} 上限"

    # 落库链路实战验证（100% 落库口径，D-31/D-33/D-34）：三表行数与终态逐项对账
    with Session(engine) as session:  # type: ignore[arg-type]
        steps = session.scalars(select(EvidenceStepRow).order_by(EvidenceStepRow.step_no)).all()
        hyps = session.scalars(select(HypothesisRow).order_by(HypothesisRow.id)).all()
        invs = session.scalars(select(Investigation)).all()
        assert len(steps) == body["step_count"]
        assert [r.step_no for r in steps] == list(range(1, len(steps) + 1))
        assert len(hyps) == len(body["hypotheses"])
        assert [h.status for h in hyps] == [h["status"] for h in body["hypotheses"]]
        assert len(invs) == 1
        inv = invs[0]
        assert inv.incident_id == incident_id
        assert inv.status == body["termination"]
        assert inv.conclusion == body["conclusion"]
        assert inv.step_count == body["step_count"]
        assert inv.failure_mode == body["failure_mode"]
        assert inv.total_cost_cny is not None
        assert inv.opening_card_json is not None  # D-37 开局锚点随行留存

    # D-28：escalated/aborted 报告同经 GET 出口可查（转人工不是丢弃）
    report = client.get(f"/investigations/{incident_id}")
    assert report.status_code == 200, report.text
    assert report.json()["termination"] == body["termination"]

    verdict = golden_support.match_root_cause(body["conclusion"], scenario)
    tool_selections = measured_planner.decisions - measured_planner.conclusions
    record = {
        "segment": "m4-08-real",
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
        "planner_decisions": measured_planner.decisions,
        "conclusions": measured_planner.conclusions,
        "output_errors": measured_planner.output_errors,
        "timeouts": measured_planner.timeouts,
        "tool_executions": measured_registry.executions,
        "param_errors": measured_registry.param_errors,
        "unknown_tools": measured_registry.unknown_tools,
        "malformed_rate": _rate(
            measured_planner.output_errors,
            measured_planner.decisions + measured_planner.output_errors + measured_planner.timeouts,
        ),
        "param_fail_rate": _rate(
            measured_registry.param_errors + measured_registry.unknown_tools, tool_selections
        ),
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
        f"duration={duration_seconds}s cost=¥{cost} match={verdict['hit']} "
        f"malformed={record['malformed_rate']} param_fail={record['param_fail_rate']}\n"
        f"conclusion: {body['conclusion']}"
    )
