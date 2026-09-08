"""ORM 行 → JSON 契约序列化器（db 层中性出口，api 与 classify 共用）。

为什么放 db 层而不是 api：alert_body 是 D-17 事件卡片本体的序列化实现，
api/card.py（查询面）与 classify/service.py（LLM 通道输入卡片）都要用——
若留在 api 包，classify → api 的 import 会与 api.routes → classify.service
形成循环（api 包 __init__ 级联拉起 routes）。下沉后依赖方向统一为
api → classify → db，M1 公开面不受影响（card.py 原样再导出）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from oncall.db import AlertEvent, Incident
from oncall.db.models import EvidenceStep as EvidenceStepRow
from oncall.db.models import Hypothesis as HypothesisRow
from oncall.db.models import Investigation
from oncall.ingest.fingerprint import as_utc

__all__ = ["alert_body", "incident_body", "investigation_report_body"]


def alert_body(row: AlertEvent) -> dict[str, Any]:
    """归一化告警本体（D-17 键名）：alert_events 行 + annotations 溯源展开。"""
    provenance = row.annotations_json or {}
    return {
        "id": row.id,
        "fingerprint": row.fingerprint,
        "source": row.source,
        "status": row.status,
        "labels": dict(row.labels_json),
        "annotations": dict(provenance.get("annotations", {})),
        # 溯源（G5/D-13）：AM 自带指纹仅交叉溯源；raw_alert/webhook 是 M3 取证输入
        "am_fingerprint": provenance.get("am_fingerprint", ""),
        "raw_alert": provenance.get("raw_alert", {}),
        "webhook": provenance.get("webhook", {}),
        "fired_at": _iso(row.fired_at),
        "last_fired_at": _iso(row.last_fired_at),
        "resolved_at": None if row.resolved_at is None else _iso(row.resolved_at),
        "dedup_count": row.dedup_count,
    }


def incident_body(row: Incident) -> dict[str, Any]:
    """incidents 最小集五字段（G5 定案）；alert_ids[0] 即 M3 调查的 primary anchor。"""
    return {
        "id": row.id,
        "alert_ids": list(row.alert_ids),
        "severity": row.severity,
        "status": row.status,
        "created_at": _iso(row.created_at),
    }


def investigation_report_body(
    row: Investigation,
    step_rows: list[EvidenceStepRow],
    hyp_rows: list[HypothesisRow],
) -> dict[str, Any]:
    """调查报告读库序列化（M4-T3 / D-35：JSON 形状 = M3 `build_report` 现契约）。

    键集合与键名逐键对齐 `api/investigation.build_report`，只换数据来源不倒改
    形状；`confidence` 沿用现口径——已裁决假设（rejected+confirmed 为分母，
    active 不计）中 confirmed 占比，无已裁决 → 0.0（与内存态无关，踩坑⑩）。
    内存契约 `cost_cny` ↔ 冻结列名 `cost`（踩坑⑧：ORM 侧 import 一律别名）。
    """
    decided = [h for h in hyp_rows if h.status != "active"]
    confirmed = sum(1 for h in decided if h.status == "confirmed")
    confidence = round(confirmed / len(decided), 4) if decided else 0.0
    return {
        "incident_id": row.incident_id,
        "termination": row.status,
        "conclusion": row.conclusion,
        "failure_mode": row.failure_mode,
        "step_count": row.step_count,
        "total_tokens": row.total_tokens,
        "total_cost_cny": row.total_cost_cny,
        "stop_reason": row.stop_reason,
        "confidence": confidence,
        "opening_card": row.opening_card_json,
        "steps": [
            {
                "step_no": s.step_no,
                "thought": s.thought,
                "tool": s.tool,
                "input_json": dict(s.input_json),
                "output_json": dict(s.output_json),
                "output_summary": s.output_summary,
                "tokens": s.tokens,
                "cost_cny": s.cost,
                "latency_ms": s.latency_ms,
                "ts": _step_ts_iso(s.ts),
            }
            for s in sorted(step_rows, key=lambda s: s.step_no)
        ],
        "hypotheses": [
            {
                "text": h.text,
                "status": h.status,
                "supporting_steps": list(h.supporting_steps),
                "against_steps": list(h.against_steps),
            }
            for h in sorted(hyp_rows, key=lambda h: h.id)  # 入池序 = 行 id 序（D-33）
        ],
    }


def _iso(moment: datetime) -> str:
    return as_utc(moment).isoformat()


def _step_ts_iso(moment: datetime) -> str:
    """步级 ts 渲染与 pydantic `model_dump(mode="json")` 同口径（D-35 roundtrip）。

    UTC 时 pydantic 输出 `Z` 后缀而 `isoformat()` 输出 `+00:00`——读库侧必须
    与内存导出逐字段一致；非 UTC 时间戳保持 isoformat 原样（现库只产 UTC）。
    """
    text = as_utc(moment).isoformat()
    return text[:-6] + "Z" if text.endswith("+00:00") else text
