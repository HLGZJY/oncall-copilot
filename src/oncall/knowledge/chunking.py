"""闭环报告 → 章节 knowledge chunks（D-53/G5：报告结构即切分边界，零 NLP 依赖）。"""

from __future__ import annotations

from typing import Any

from oncall.knowledge.report import KB_SECTIONS

__all__ = ["chunk_report"]


def chunk_report(
    sections: dict[str, Any],
    *,
    incident_id: int,
    investigation_id: int | None,
) -> list[dict[str, Any]]:
    """五节闭环报告 → 块列表（`kb_chunks` 行字段形状，不含 id/时间戳）。

    每节 1 块（章节级粒度）；空文本节跳过（无源不虚构——空节入库只会召回噪声）；
    每块带 incident_id/section/seq 元数据 + source 锚点透传（D-49 回溯链不断）。
    """
    chunks: list[dict[str, Any]] = []
    for section, seq in _section_seq_map().items():
        body = sections.get(section)
        if not body or not str(body.get("text", "")).strip():
            continue
        chunks.append(
            {
                "incident_id": incident_id,
                "investigation_id": investigation_id,
                "section": section,
                "seq": seq,
                "text": str(body["text"]),
                "source_meta_json": {"source": body.get("source", {})},
            }
        )
    return chunks


def _section_seq_map() -> dict[str, int]:
    """KB_SECTIONS 顺序 → seq（从 0 递增；与 kb_chunks.section 五值同词表）。"""
    return {section: idx for idx, section in enumerate(KB_SECTIONS)}
