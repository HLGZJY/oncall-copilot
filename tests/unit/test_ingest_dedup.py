"""T3 验收测试：时间窗去重合并（issue 03 / G2 定案）。

契约来源：docs/design/m1-alert-ingestion-design.md G2（去重状态落 DB 行、不引 Redis）
+ G1（时间窗默认 10m，dedup_window 可调）+ D-13（幂等硬要求）。

覆盖：
- 同故障 3 连发 → 1 条记录 dedup_count=3，0 漏收（每次 firing 都计入）
- 窗口边界：窗内合并 / 超窗新行 / dedup_window 配置生效
- resolved 到达 → resolved_at 落且不计数；窗内再触发 → 重新打开
- 幂等回归：同 payload 重放 dedup_count 不变（issue 02 硬要求不被破坏）
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, select
from sqlalchemy.orm import Session

from conftest import make_alert, make_webhook
from oncall.db import AlertEvent, create_tables
from oncall.ingest.app import create_app
from oncall.ingest.fingerprint import DEFAULT_DEDUP_WINDOW
from oncall.ingest.schemas import AlertmanagerWebhook
from oncall.ingest.service import ingest_webhook

T0 = datetime(2026, 9, 6, 6, 0, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _aware(moment: datetime) -> datetime:
    """SQLite 取回的时间无 tzinfo，统一补 UTC 后比较。"""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _ingest(session: Session, alerts: list[dict], window: timedelta = DEFAULT_DEDUP_WINDOW):
    webhook = AlertmanagerWebhook.model_validate(make_webhook(alerts=alerts))
    return ingest_webhook(session, webhook, window)


def _firing(offset: timedelta = timedelta(), **overrides) -> dict:
    return make_alert(startsAt=_iso(T0 + offset), **overrides)


def _resolved(offset: timedelta = timedelta(), ends: timedelta = timedelta(seconds=20)) -> dict:
    return make_alert(
        status="resolved", startsAt=_iso(T0 + offset), endsAt=_iso(T0 + offset + ends)
    )


def _rows(session: Session) -> list[AlertEvent]:
    return list(session.scalars(select(AlertEvent).order_by(AlertEvent.id)).all())


class TestMergeInWindow:
    def test_three_consecutive_firings_merge_into_one_row(self):
        """同故障 3 连发（startsAt 递增）→ 1 行 dedup_count=3，last_fired_at 刷新到最新。"""
        with Session(_engine()) as session:
            results = [
                _ingest(session, [_firing(timedelta(minutes=offset))]) for offset in (0, 1, 2)
            ]
            rows = _rows(session)

        assert [r.received for r in results] == [1, 1, 1]
        assert [r.deduped for r in results] == [0, 1, 1]
        assert len(rows) == 1
        assert rows[0].dedup_count == 3
        assert _aware(rows[0].fired_at) == T0  # 首 firing 时间不动
        assert _aware(rows[0].last_fired_at) == T0 + timedelta(minutes=2)

    def test_zero_loss_every_firing_counted(self):
        """0 漏收：3 次 firing 全部计入（1 次入库 + 2 次合并计数）。"""
        with Session(_engine()) as session:
            for offset in (0, 1, 2):
                _ingest(session, [_firing(timedelta(minutes=offset))])
            rows = _rows(session)
        assert sum(r.dedup_count for r in rows) == 3

    def test_beyond_window_creates_new_row(self):
        """超窗（11 分钟后）→ 新时间窗桶 → 新行，两行各自计数 1。"""
        with Session(_engine()) as session:
            _ingest(session, [_firing()])
            _ingest(session, [_firing(timedelta(minutes=11))])
            rows = _rows(session)

        assert len(rows) == 2
        assert [r.dedup_count for r in rows] == [1, 1]

    def test_dedup_window_configurable(self):
        """dedup_window=1m：距上次 firing 30s → 合并；距上次 65s（>窗）→ 分行。"""
        with Session(_engine()) as session:
            for offset in (timedelta(), timedelta(seconds=30), timedelta(seconds=95)):
                _ingest(session, [_firing(offset)], timedelta(minutes=1))
            rows = _rows(session)

        assert len(rows) == 2
        assert [r.dedup_count for r in rows] == [2, 1]


class TestIdempotencyRegression:
    def test_same_payload_replay_keeps_dedup_count(self):
        """同一 payload 原样重放 3 次 → 1 行，dedup_count 不变（D-13 幂等）。"""
        payload = [_firing()]
        with Session(_engine()) as session:
            for _ in range(3):
                _ingest(session, payload)
            rows = _rows(session)

        assert len(rows) == 1
        assert rows[0].dedup_count == 1

    def test_replay_after_merges_keeps_count(self):
        """已合并 3 连发的行，再重放最后一次 firing → 计数仍为 3（不重复计数）。"""
        with Session(_engine()) as session:
            for offset in (0, 1, 2):
                _ingest(session, [_firing(timedelta(minutes=offset))])
            _ingest(session, [_firing(timedelta(minutes=2))])
            rows = _rows(session)

        assert len(rows) == 1
        assert rows[0].dedup_count == 3


class TestResolvedLinkage:
    def test_resolved_sets_resolved_at_without_counting(self):
        """resolved 通知：resolved_at 落，dedup_count 不增（不是新 firing）。"""
        with Session(_engine()) as session:
            _ingest(session, [_firing()])
            _ingest(session, [_resolved()])
            rows = _rows(session)

        assert len(rows) == 1
        assert rows[0].dedup_count == 1
        assert _aware(rows[0].resolved_at) == T0 + timedelta(seconds=20)

    def test_late_resolved_still_finds_original_row(self):
        """resolved 迟到 45 分钟也能找回原行（指纹锚在 startsAt，与到达时间无关）。"""
        with Session(_engine()) as session:
            _ingest(session, [_firing()])
            late = make_alert(
                status="resolved", startsAt=_iso(T0), endsAt=_iso(T0 + timedelta(minutes=45))
            )
            _ingest(session, [late])
            rows = _rows(session)

        assert len(rows) == 1
        assert _aware(rows[0].resolved_at) == T0 + timedelta(minutes=45)

    def test_duplicate_resolved_is_idempotent(self):
        """重复 resolved 不把 resolved_at 改写为更早时间。"""
        with Session(_engine()) as session:
            _ingest(session, [_firing()])
            _ingest(session, [_resolved(ends=timedelta(seconds=40))])
            _ingest(session, [_resolved(ends=timedelta(seconds=20))])
            rows = _rows(session)

        assert _aware(rows[0].resolved_at) == T0 + timedelta(seconds=40)

    def test_new_firing_after_resolution_reopens_row(self):
        """窗内 resolved 后再触发 → 重新打开（resolved_at 清空，计数 ++）。"""
        with Session(_engine()) as session:
            _ingest(session, [_firing()])
            _ingest(session, [_resolved(ends=timedelta(minutes=5))])
            _ingest(session, [_firing(timedelta(minutes=7))])
            rows = _rows(session)

        assert len(rows) == 1
        assert rows[0].dedup_count == 2
        assert rows[0].resolved_at is None
        assert _aware(rows[0].last_fired_at) == T0 + timedelta(minutes=7)


@pytest.mark.inproc_asgi
class TestEndpointWindowConfig:
    def test_dedup_window_config_applies_end_to_end(self):
        """端点层窗口配置生效：1 分钟窗下 2 分钟后的 firing 分属两行。"""
        engine = _engine()
        client = TestClient(create_app(engine, dedup_window=timedelta(minutes=1)))
        client.post("/ingest", json=make_webhook(alerts=[_firing()]))
        client.post("/ingest", json=make_webhook(alerts=[_firing(timedelta(minutes=2))]))

        with Session(engine) as session:
            assert len(_rows(session)) == 2


def _engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return engine
