"""闭环报告拼装（D-49/G1：读库确定性拼装、禁虚构、不落报告表）。

五节 = 开局卡片摘要 / 时间线 / 根因 / 处置 / 改进建议（D-53 同款 section 词表，
与 kb_chunks.section 五值冻结对齐）。每个数据点携带 `source` 锚点（表名 + 行 id），
经其可回溯库行——「报告数据 100% 已在各表，落报告表 = 可漂移的第二副本」
（D-49），本模块只读不写。

改进建议节（D-50/G2）：LLM 生成、双门槛门控——env `ONCALL_KB_SUGGESTIONS_LLM`
缺省关（测试/CI 全走占位文案）；真实调用是 key 门槛票单独拍板，本票冻结
mock 契约（输入 = 证据链摘要 dict）与占位出口。
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oncall.db.models import AlertEvent, Investigation, RemediationProposal
from oncall.db.models import (
    EvidenceStep as EvidenceStepRow,
)
from oncall.db.models import (
    Hypothesis as HypothesisRow,
)
from oncall.db.models import (
    Incident as IncidentRow,
)
from oncall.db.views import investigation_report_body
from oncall.ingest.fingerprint import as_utc

__all__ = [
    "KB_SECTIONS",
    "SUGGESTIONS_PLACEHOLDER",
    "build_closed_loop_report",
    "render_closed_loop_markdown",
]

# D-53 五类 section（与 kb_chunks.section CHECK 约束同词表）
KB_SECTIONS = ("opening_card", "timeline", "root_cause", "remediation", "suggestions")

# D-50：LLM 未启用时的占位文案（显式标注 AI 生成节未启用，不伪造成确定性内容）
SUGGESTIONS_PLACEHOLDER = "改进建议：见 runbook 正文 / 待人工补充（LLM 建议生成未启用）"


def _iso(moment: Any) -> str:
    """datetime → ISO 字符串（None 透传；时区口径照 views.alert_body 先例）。"""
    return "" if moment is None else as_utc(moment).isoformat()


def build_closed_loop_report(session: Session, incident_id: int) -> dict[str, Any]:
    """读库拼装五节闭环报告（D-49：每节带 source 锚点，禁虚构）。

    返回 `{section: {"text": ..., "source": {...}}}`；无调查记录 → KeyError 由
    调用方转 404（与 M4 GET 出口 404 语义一致）；`root_cause` 无 confirmed 假设
    时如实落「无已证实假设」——不挑数据不虚构；时间线逐条锚 alert_events 行 id。
    """
    inv_row = session.scalar(select(Investigation).where(Investigation.incident_id == incident_id))
    if inv_row is None or inv_row.status == "running":
        raise KeyError(f"无调查报告: incident_id={incident_id}")

    # alert_ids 来自 incidents 表（D-19 五字段，primary anchor = alert_ids[0]）
    incident_row = session.get(IncidentRow, incident_id)
    incident_alert_ids = list(incident_row.alert_ids) if incident_row is not None else []

    # ── opening_card 节：investigations.opening_card_json 随行留存（D-17 卡片）──
    opening = inv_row.opening_card_json or {}
    opening_text = _render_opening_summary(opening)

    # ── 时间线节：alert_events 实测 firing 序列（逐条锚行 id，禁虚构）──
    timeline_rows: list[dict[str, Any]] = []
    timeline_anchors: list[int] = []
    for alert_id in incident_alert_ids:
        row = session.get(AlertEvent, alert_id)
        if row is None:
            continue
        timeline_rows.append(
            {
                "alert_id": row.id,
                "alertname": row.labels_json.get("alertname", ""),
                "instance": row.labels_json.get("instance", ""),
                "status": row.status,
                "fired_at": _iso(row.fired_at),
                "last_fired_at": _iso(row.last_fired_at),
                "resolved_at": _iso(row.resolved_at),
                "dedup_count": row.dedup_count,
            }
        )
        timeline_anchors.append(row.id)

    # ── 根因节：confirmed 假设 + 支持证据步摘要（来源锚 hypotheses 行）──
    hyp_rows = list(
        session.scalars(select(HypothesisRow).where(HypothesisRow.incident_id == incident_id))
    )
    confirmed = [h for h in hyp_rows if h.status == "confirmed"]
    root_cause_text = _render_confirmed(confirmed)

    # ── 处置节：remediation_proposals 全链（D-46 留痕直出）──
    proposal_rows = list(
        session.scalars(
            select(RemediationProposal).where(RemediationProposal.incident_id == incident_id)
        )
    )
    remediation_lines = [
        "- proposal#{id} [{status}] {slug}/{action} 决策={decision} 回滚={rollback}".format(
            id=p.id,
            status=p.status,
            slug=p.runbook_slug,
            action=p.action_id,
            decision=p.decision or "—",
            rollback=p.rollback_status or "—",
        )
        for p in sorted(proposal_rows, key=lambda p: p.id)
    ] or ["无处置提案"]

    # ── 改进建议节：D-50 双门槛门控（env 缺省关 → 占位文案）──
    summary = {
        "termination": inv_row.status,
        "conclusion": inv_row.conclusion,
        "hypotheses": root_cause_text,
    }
    suggestions = _suggestions_section(summary)

    # 证据链报告 body（M4 契约）附在五节之后由调用方渲染；此处只产五节
    return {
        "opening_card": {
            "text": opening_text,
            "source": {"investigations": [inv_row.id], "field": "opening_card_json"},
        },
        "timeline": {
            "text": "\n".join(
                "- [{status}] {alertname}@{instance} fired={fired_at} last={last_fired_at}"
                " resolved={resolved_at} dedup={dedup_count} (alert#{alert_id})".format(**r)
                for r in timeline_rows
            )
            or "无关联告警",
            "source": {"alert_events": timeline_anchors},
        },
        "root_cause": {
            "text": root_cause_text,
            "source": {"hypotheses": [h.id for h in hyp_rows if h.status == "confirmed"]},
        },
        "remediation": {
            "text": "\n".join(remediation_lines),
            "source": {"remediation_proposals": [p.id for p in proposal_rows]},
        },
        "suggestions": {
            "text": suggestions,
            "source": {"generated_by": "llm-gated" if _suggestions_enabled() else "placeholder"},
        },
    }


def _render_opening_summary(opening: dict[str, Any]) -> str:
    """D-17 卡片精简投影（对齐 D-37 view 投影口径，不塞 items 全文）。"""
    alert = opening.get("alert", {})
    if not alert:
        return "无开局卡片"
    context = opening.get("context", {})
    return (
        "告警 {alertname}@{instance} severity={severity} source={source} "
        "status={status} fired={fired} last={last}；上下文三源状态：{ctx}".format(
            alertname=alert.get("labels", {}).get("alertname", alert.get("alertname", "")),
            instance=alert.get("labels", {}).get("instance", alert.get("instance", "")),
            severity=alert.get("severity", "—"),
            source=alert.get("source", "—"),
            status=alert.get("status", "—"),
            fired=alert.get("fired_at", "—"),
            last=alert.get("last_fired_at", "—"),
            ctx=json_compact_status(context),
        )
    )


def _render_confirmed(confirmed: list[HypothesisRow]) -> str:
    """confirmed 假设 → 根因节文本；无 confirmed 如实落「无已证实假设」（不虚构）。"""
    if not confirmed:
        return "无已证实假设"
    lines = (
        f"- {h.text}（支持步: {', '.join(str(n) for n in h.supporting_steps) or '无'}）"
        for h in confirmed
    )
    return "\n".join(lines)


def json_compact_status(context: dict[str, Any]) -> str:
    """三源 context 状态摘要（source→status 对，D-16 形状）。"""
    if not context:
        return "无"
    parts = (
        f"{src}={body.get('status', '—')}"
        for src, body in context.items()
        if isinstance(body, dict)
    )
    return " ".join(parts)


def _suggestions_enabled() -> bool:
    return os.environ.get("ONCALL_KB_SUGGESTIONS_LLM", "").strip().lower() in {"1", "true", "yes"}


def _suggestions_section(evidence_summary: dict[str, Any]) -> str:
    """D-50 mock 契约冻结点：输入 = 证据链摘要，输出 = 结构化建议文本。

    env 开关关 → 占位文案（真实 LLM 调用是 key 门槛票，单独拍板后在此注换）。
    """
    if not _suggestions_enabled():
        return SUGGESTIONS_PLACEHOLDER
    # 真实 LLM 分支留待 key 门槛票：此处永不静默降级，未启用即占位（R6 语义）
    return SUGGESTIONS_PLACEHOLDER


SECTION_TITLES = {
    "opening_card": "开局卡片",
    "timeline": "时间线",
    "root_cause": "根因",
    "remediation": "处置",
    "suggestions": "改进建议",
}


def render_closed_loop_markdown(sections: dict[str, Any]) -> str:
    """五节 dict → Markdown（报告结构即切分边界，D-53；数据直出零美化虚构）。"""
    parts = ["\n## 闭环报告\n"]
    for section in KB_SECTIONS:
        body = sections[section]
        parts.append(f"### {SECTION_TITLES[section]}\n\n{body['text']}\n")
    return "\n".join(parts)


def evidence_chain_markdown(session: Session, incident_id: int) -> str:
    """M4 证据链最小版（D-36 出口）读库重放——供闭环报告前置拼接。

    与 api._render_report_markdown 同口径但数据源在 knowledge 侧复用
    `investigation_report_body`（D-35 权威序列化器），避免 api 内部函数外溢。
    """
    inv_row = session.scalar(select(Investigation).where(Investigation.incident_id == incident_id))
    if inv_row is None or inv_row.status == "running":
        raise KeyError(f"无调查报告: incident_id={incident_id}")
    step_rows = list(
        session.scalars(
            select(EvidenceStepRow)
            .where(EvidenceStepRow.incident_id == incident_id)
            .order_by(EvidenceStepRow.step_no)
        )
    )
    hyp_rows = list(
        session.scalars(select(HypothesisRow).where(HypothesisRow.incident_id == incident_id))
    )
    body = investigation_report_body(inv_row, step_rows, hyp_rows)
    lines = [
        f"# 调查报告 incident_id={body['incident_id']}",
        f"- termination: {body['termination']}",
        f"- conclusion: {body['conclusion']}",
        f"- failure_mode: {body['failure_mode']}",
        f"- step_count: {body['step_count']}",
        f"- total_tokens: {body['total_tokens']}",
        f"- total_cost_cny: {body['total_cost_cny']}",
        f"- stop_reason: {body['stop_reason']}",
        f"- confidence: {body['confidence']}",
        "",
        "## 假设",
    ]
    for hyp in body["hypotheses"]:
        supporting = ", ".join(str(n) for n in hyp["supporting_steps"]) or "无"
        against = ", ".join(str(n) for n in hyp["against_steps"]) or "无"
        lines.append(
            f"- [{hyp['status']}] {hyp['text']}（支持步: {supporting}｜反对步: {against}）"
        )
    return "\n".join(lines) + "\n"
