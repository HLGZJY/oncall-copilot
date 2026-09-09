"""确认门 API 契约测试（M5 issue 04 / T4 / G2 / D-40 / D-48）。

契约来源：docs/design/m5-remediation-gates-design.md §API 契约表 +
decisions.md **D-40**（confirm 即执行——approve 同步走受控执行 + 恢复验证返回
终态；提案不自动过期）/ **D-48**（降级形态：未注入受控执行器 → approve 落
503 + proposal 留 approved 即终，只砍注入面不改契约）/ **D-46**（第八表字段
= GET 序列化形状）/ D-44/D-45（06 语义——本票只注入替身，未恢复落 failed）。

覆盖（对应 issue 04 验收①–⑦）：
- confirm approve → 注入替身执行器/验证器调用断言（收到的命令清单 =
  dry_run_json 原文）→ 同步返回终态行（恢复 → recovered / 未恢复 → failed）
- confirm reject → proposal 落 rejected + decision/reason/confirmed_at 落库
- GET 单个 / GET by incident（多次尝试两行都在，按 id 序）
- 4xx 语义：不存在 404、已终态再 confirm 409、decision 非法 422
- 降级（D-48）：未注入执行器 approve → 503 + proposal 留 approved
- dry_run_json 全路径不可变在 API 层复验（走完 confirm 后重读 deep-equal）

零 LLM、零真实 HTTP 外呼：TestClient 进程内 ASGI（inproc_asgi 豁免，照
test_investigation_api.py 先例）；执行器/验证器全为测试替身（本票不实装，
归 issue 05/06）。
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, event
from sqlalchemy.orm import Session

from oncall.api.remediation import RemediationDeps
from oncall.db import Incident, create_tables
from oncall.db.models import RemediationProposal
from oncall.ingest.app import create_app
from oncall.remediation.service import create_proposal

pytestmark = pytest.mark.inproc_asgi

# issue 02 定稿的 dry_run_json 形状（execute.py preview 结构，与
# test_remediation_service.py 同源）
DRY_RUN: dict[str, Any] = {
    "runbook_slug": "slow-sql",
    "action_id": "kill-lock-session",
    "action_name": "KILL 持锁会话",
    "commands": [
        {
            "step": 1,
            "action": "mysql.kill_session",
            "command": "mysql.kill_session conn_id=$conn_id",
            "impact": "KILL demo MySQL 内持锁/睡眠会话以释放表锁（幂等）",
            "runtime_params": ["conn_id"],
        }
    ],
    "impact": "KILL demo MySQL 内持锁/睡眠会话以释放表锁（幂等）",
}

PROPOSAL_KEYS = {
    "id",
    "incident_id",
    "investigation_id",
    "runbook_slug",
    "action_id",
    "status",
    "dry_run_json",
    "params_json",
    "decision",
    "confirm_reason",
    "confirmed_at",
    "executed_at",
    "verify_result_json",
    "rollback_status",
    "created_at",
    "finished_at",
}


# ---------------------------------------------------------------------------
# 注入替身（本票不实装受控执行器/恢复验证器——调用断言即可）
# ---------------------------------------------------------------------------


class StubExecutor:
    """受控执行器替身：记录收到的命令清单，返回执行输出摘要。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, dry_run_json: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(dry_run_json))
        return {"executed_steps": len(dry_run_json.get("commands", [])), "output": "ok"}


class StubVerifier:
    """恢复验证器替身：按构造参数返回恢复/未恢复判定。"""

    def __init__(self, *, recovered: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self._recovered = recovered

    def verify(self, dry_run_json: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(dry_run_json))
        return {"recovered": self._recovered, "detail": "stub 判定"}


# ---------------------------------------------------------------------------
# 装配（照 test_investigation_api.py：内存库 + StaticPool + PRAGMA + TestClient）
# ---------------------------------------------------------------------------


def _make_engine() -> Any:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_tables(engine)
    return engine


def _seed_incident(engine: Any) -> int:
    with Session(engine) as session:
        incident = Incident(alert_ids=["a" * 64], severity="critical", status="investigating")
        session.add(incident)
        session.commit()
        return incident.id


def _create_proposal(engine: Any, incident_id: int, **overrides: Any) -> int:
    fields: dict[str, Any] = {
        "incident_id": incident_id,
        "runbook_slug": "slow-sql",
        "action_id": "kill-lock-session",
        "dry_run_json": copy.deepcopy(DRY_RUN),
    }
    fields.update(overrides)
    with Session(engine) as session:
        return create_proposal(session, **fields)


def _make_client(deps: RemediationDeps | None = None) -> tuple[TestClient, Any]:
    engine = _make_engine()
    app = create_app(engine, remediation=deps if deps is not None else RemediationDeps())
    return TestClient(app), engine


def _reload(engine: Any, proposal_id: int) -> RemediationProposal:
    with Session(engine) as session:
        return session.get(RemediationProposal, proposal_id)


# ---------------------------------------------------------------------------
# confirm approve：同步执行链（验收①⑥）
# ---------------------------------------------------------------------------


class TestConfirmApprove:
    def test_approve_runs_sync_chain_and_returns_terminal_row(self):
        executor, verifier = StubExecutor(), StubVerifier(recovered=True)
        client, engine = _make_client(RemediationDeps(executor=executor, verifier=verifier))
        incident_id = _seed_incident(engine)
        pid = _create_proposal(engine, incident_id)

        resp = client.post(
            f"/remediations/{pid}/confirm", json={"decision": "approve", "reason": "影响面已核对"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == PROPOSAL_KEYS
        assert body["status"] == "recovered"
        assert body["decision"] == "approve"
        assert body["confirm_reason"] == "影响面已核对"
        assert body["confirmed_at"] and body["executed_at"] and body["finished_at"]
        assert body["verify_result_json"] == {"recovered": True, "detail": "stub 判定"}
        # 执行输出摘要落 params_json（执行留痕载体）
        assert body["params_json"]["execution"] == {"executed_steps": 1, "output": "ok"}
        # 替身调用断言：执行器/验证器收到的命令清单 = dry_run_json 原文（批准对象锁定）
        assert executor.calls == [DRY_RUN]
        assert verifier.calls == [DRY_RUN]
        # 库内终态行复核
        row = _reload(engine, pid)
        assert row.status == "recovered" and row.decision == "approve"

    def test_approve_not_recovered_lands_failed(self):
        client, engine = _make_client(
            RemediationDeps(executor=StubExecutor(), verifier=StubVerifier(recovered=False))
        )
        incident_id = _seed_incident(engine)
        pid = _create_proposal(engine, incident_id)

        resp = client.post(f"/remediations/{pid}/confirm", json={"decision": "approve"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

    def test_dry_run_json_immutable_through_full_chain(self):
        client, engine = _make_client(
            RemediationDeps(executor=StubExecutor(), verifier=StubVerifier(recovered=True))
        )
        incident_id = _seed_incident(engine)
        pid = _create_proposal(engine, incident_id)

        client.post(f"/remediations/{pid}/confirm", json={"decision": "approve"})

        row = _reload(engine, pid)
        assert dict(row.dry_run_json) == DRY_RUN  # 走完 confirm 链 deep-equal（验收⑥）


# ---------------------------------------------------------------------------
# confirm reject（验收②）与 4xx 语义（验收⑤）
# ---------------------------------------------------------------------------


class TestConfirmRejectAndErrors:
    def test_reject_lands_rejected_with_reason(self):
        client, engine = _make_client(RemediationDeps())
        incident_id = _seed_incident(engine)
        pid = _create_proposal(engine, incident_id)

        resp = client.post(
            f"/remediations/{pid}/confirm", json={"decision": "reject", "reason": "误报不处置"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["decision"] == "reject"
        assert body["confirm_reason"] == "误报不处置"
        assert body["confirmed_at"]
        row = _reload(engine, pid)
        assert row.status == "rejected" and row.confirm_reason == "误报不处置"

    def test_confirm_unknown_proposal_404(self):
        client, _engine = _make_client(RemediationDeps())
        resp = client.post("/remediations/999/confirm", json={"decision": "approve"})
        assert resp.status_code == 404

    def test_confirm_on_terminal_state_409(self):
        client, engine = _make_client(RemediationDeps())
        incident_id = _seed_incident(engine)
        pid = _create_proposal(engine, incident_id)
        client.post(f"/remediations/{pid}/confirm", json={"decision": "reject"})

        resp = client.post(f"/remediations/{pid}/confirm", json={"decision": "approve"})

        assert resp.status_code == 409

    def test_bad_decision_422(self):
        client, engine = _make_client(RemediationDeps())
        incident_id = _seed_incident(engine)
        pid = _create_proposal(engine, incident_id)
        resp = client.post(f"/remediations/{pid}/confirm", json={"decision": "maybe"})
        assert resp.status_code == 422
        extra = client.post(
            f"/remediations/{pid}/confirm", json={"decision": "approve", "extra": 1}
        )
        assert extra.status_code == 422  # extra=forbid


# ---------------------------------------------------------------------------
# GET 查询（验收③④）
# ---------------------------------------------------------------------------


class TestGetEndpoints:
    def test_get_single_returns_full_row(self):
        client, engine = _make_client(RemediationDeps())
        incident_id = _seed_incident(engine)
        pid = _create_proposal(engine, incident_id)

        resp = client.get(f"/remediations/{pid}")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == PROPOSAL_KEYS
        assert body["status"] == "pending"
        assert body["dry_run_json"] == DRY_RUN  # 干跑预览原文
        assert body["verify_result_json"] is None and body["rollback_status"] is None

    def test_get_unknown_404(self):
        client, _engine = _make_client(RemediationDeps())
        assert client.get("/remediations/999").status_code == 404

    def test_get_by_incident_returns_all_attempts_in_id_order(self):
        client, engine = _make_client(RemediationDeps())
        incident_id = _seed_incident(engine)
        pid1 = _create_proposal(engine, incident_id)
        pid2 = _create_proposal(
            engine, incident_id, runbook_slug="cpu-spike", action_id="stop-stress"
        )

        resp = client.get("/remediations", params={"incident_id": incident_id})

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert [item["id"] for item in body["items"]] == [pid1, pid2]
        assert {item["runbook_slug"] for item in body["items"]} == {"slow-sql", "cpu-spike"}


# ---------------------------------------------------------------------------
# 降级路径（验收⑦ / D-48）
# ---------------------------------------------------------------------------


class TestDegradePath:
    def test_approve_without_executor_503_and_stays_approved(self):
        client, engine = _make_client(RemediationDeps(executor=None, verifier=None))
        incident_id = _seed_incident(engine)
        pid = _create_proposal(engine, incident_id)

        resp = client.post(f"/remediations/{pid}/confirm", json={"decision": "approve"})

        assert resp.status_code == 503
        assert "执行能力未配置" in resp.json()["detail"]
        row = _reload(engine, pid)
        assert row.status == "approved"  # 建议已确认是可审计事实，留 approved 即终
        assert row.decision == "approve"
        # approved 后再 confirm → 409（approved 无出边到 approved/rejected）
        again = client.post(f"/remediations/{pid}/confirm", json={"decision": "approve"})
        assert again.status_code == 409

    def test_reject_still_works_in_degrade_mode(self):
        client, engine = _make_client(RemediationDeps(executor=None, verifier=None))
        incident_id = _seed_incident(engine)
        pid = _create_proposal(engine, incident_id)

        resp = client.post(f"/remediations/{pid}/confirm", json={"decision": "reject"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
