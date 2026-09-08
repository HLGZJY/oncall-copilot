"""M4-T7 验收断言收口（m4 issue 07）——设计文档「验收标准」节逐条机械断言。

本文件是断言汇总票的落点：01–06 各票单测覆盖 repo/API/视图层，此处补
「mock 3 剧本经 POST /investigate 走至 conclusion 后的 DB 落库收口」——

- 验收①（100% 落库）：evidence_steps 行数 = 会话步数（= 响应 step_count）、
  hypotheses 行数 = 假设池大小、investigations 恰一行且终态与响应一致
- 验收②（任意一步可回溯）：每行 output_json 键集合 = {status, data, meta}，
  与响应体对应步逐字段一致（POST 响应 = build_report 口径，GET 读库
  roundtrip 已由 test_investigation_api 证等，故此处 DB 行 ↔ 响应步对账
  即 ToolResult status/data/meta 逐字段回溯）；D-28：escalated/aborted
  已取证部分可查由 test_db_evidence_repo / test_investigation_api 覆盖
- 验收②补强（ToolResult 逐字段口径）：自定义 handler 返回已知 ToolResult，
  落库后 output_json 与该 ToolResult 三字段精确对账（非响应体间接）

零 LLM 真实调用、零 HTTP 外呼（golden 替身 + MockPlanner，D-29）。
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from oncall.harness.loop import LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner, PlannerDecision
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.tools.schemas import ToolResult, ToolStatus
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict
from oncall.ingest.app import create_app

pytestmark = pytest.mark.inproc_asgi

SCENARIOS = ["cpu-spike", "slow-sql", "queue-backlog"]

#: 验收②键集合（架构 §4 / loop.py 装配口径，D-35 冻结契约）
OUTPUT_JSON_KEYS = {"status", "data", "meta"}


def _make_engine() -> Any:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return engine


def _seed_incident(engine: Any, scenario: str) -> int:
    """建档：golden 首条告警落 alert_events + 1 条 incident（照 e2e 先例）。"""
    entry = golden_support.load_golden(scenario)["runs"][0]["alert_timeline"][0]
    with Session(engine) as session:
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


# ---------------------------------------------------------------------------
# 验收① + ②：mock 3 剧本 POST /investigate → DB 落库收口
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_three_scenarios_full_persistence_after_post(scenario: str):
    """验收①+②：3 剧本走完 conclusion 后三表行数/终态/output_json 收口断言。"""
    doc = golden_support.load_golden(scenario)
    engine = _make_engine()
    incident_id = _seed_incident(engine, scenario)
    app = create_app(
        engine,
        investigation=InvestigationDeps(
            components=golden_support.make_e2e_components(
                golden_support.make_mock_planner(doc), doc
            )
        ),
    )
    client = TestClient(app)

    resp = client.post("/investigate", json={"incident_id": incident_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["termination"] == "concluded"

    with Session(engine) as session:
        steps = session.scalars(select(EvidenceStepRow).order_by(EvidenceStepRow.step_no)).all()
        hyps = session.scalars(select(HypothesisRow).order_by(HypothesisRow.id)).all()
        invs = session.scalars(select(Investigation)).all()

        # 验收①：行数 = 会话步数 / 假设池大小；investigations 恰一行且终态一致
        assert len(steps) == body["step_count"]
        assert [r.step_no for r in steps] == list(range(1, len(steps) + 1))
        assert len(hyps) == len(body["hypotheses"])
        assert [h.status for h in hyps] == [h["status"] for h in body["hypotheses"]]
        assert len(invs) == 1
        inv = invs[0]
        assert inv.incident_id == incident_id
        assert inv.status == body["termination"]
        assert inv.conclusion == body["conclusion"] == doc["root_cause"]
        assert inv.step_count == body["step_count"]
        assert inv.failure_mode == body["failure_mode"]

        # 验收②：任一行 output_json 与响应步（= ToolResult status/data/meta）逐字段一致
        assert [s["step_no"] for s in body["steps"]] == [r.step_no for r in steps]
        for row, step in zip(steps, body["steps"], strict=True):
            assert set(row.output_json) == OUTPUT_JSON_KEYS
            assert row.output_json == step["output_json"]
            assert row.output_json["meta"]["source"] == "golden-dev-timeline"


# ---------------------------------------------------------------------------
# 验收②补强：output_json 与已知 ToolResult 三字段精确对账
# ---------------------------------------------------------------------------


def _probe_handler(_args: Any, *, timeout_seconds: float) -> ToolResult:
    """已知 ToolResult：data/meta 带特征值，供落库后逐字段精确对账。"""
    return ToolResult(
        tool="query_metrics",
        status=ToolStatus.OK,
        data={"direction": "up", "marker": "m4-t7"},
        meta={"probe": "acceptance", "run": 7},
    )


def _tool_result_components() -> LoopComponents:
    now = lambda: datetime.now(UTC)  # noqa: E731
    registry = ToolRegistry(now=now)
    register_six_tools(
        registry,
        {
            name: _probe_handler
            for name in (
                "query_metrics",
                "search_logs",
                "detect_anomaly",
                "get_topology",
            )
        },
    )
    return LoopComponents(
        planner=MockPlanner(
            script=[
                PlannerDecision.model_validate(
                    {
                        "thought": "查 CPU 水位",
                        "next_tool": "query_metrics",
                        "args": {
                            "promql": "up",
                            "start": now().isoformat(),
                            "end": now().isoformat(),
                        },
                    }
                ),
                PlannerDecision.model_validate({"thought": "收束", "conclusion": "探针定案"}),
            ]
        ),
        registry=registry,
        gate=PermissionGate(now=now),
        verifier=Verifier(
            judge=MockVerifierJudge(
                script=None, default=VerifierVerdict(supported=True, reason="mock 支持")
            )
        ),
        now=now,
    )


def test_output_json_matches_known_tool_result_field_by_field():
    """验收②补强：DB 行 output_json 与该步 ToolResult（status/data/meta）精确一致。"""
    engine = _make_engine()
    incident_id = _seed_incident(engine, "cpu-spike")
    app = create_app(engine, investigation=InvestigationDeps(components=_tool_result_components()))
    client = TestClient(app)

    resp = client.post("/investigate", json={"incident_id": incident_id})
    assert resp.status_code == 200
    assert resp.json()["termination"] == "concluded"

    with Session(engine) as session:
        rows = session.scalars(select(EvidenceStepRow).order_by(EvidenceStepRow.step_no)).all()
    assert len(rows) == 1
    assert rows[0].output_json == {
        "status": "ok",
        "data": {"direction": "up", "marker": "m4-t7"},
        "meta": {"probe": "acceptance", "run": 7},
    }
