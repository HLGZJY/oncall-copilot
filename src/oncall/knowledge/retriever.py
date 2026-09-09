"""query_kb 真实 RAG 实装（D-23 stub 兑现）：检索器 + registry handler 注入面。

C3 纪律：harness 零 import knowledge——本模块产出 `ToolHandler` 形状的函数，
由组装点（app.py）经 `register_six_tools` 既有 handlers 注入面传入；未注入时
registry 维持 `query_kb_stub` unavailable 语义（既有测试零回退）。
检索结果语义永远是「历史相似案例（参考）」——kb 证据不可独立证实假设（T5 纪律）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from oncall.db.models import KbChunk
from oncall.harness.tools.registry import ToolTimeoutError
from oncall.harness.tools.schemas import ToolResult, ToolStatus

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from oncall.knowledge.embedder import Embedder
    from oncall.knowledge.store import VectorStore

__all__ = ["KbHit", "KbRetriever", "build_query_kb_handler"]


@dataclass(frozen=True)
class KbHit:
    """一条召回：块 id / 事件锚 / 章节 / 文本 / 相似分；source=kb 是污染防线语义标记。"""

    chunk_id: int
    incident_id: int
    section: str
    text: str
    score: float
    source: str = "kb"

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "incident_id": self.incident_id,
            "section": self.section,
            "text": self.text,
            "score": self.score,
            "source": self.source,
        }


class KbRetriever:
    """向量检索器：query → 向量索引 top_k → 读权威表取文本（D-55 权威划分）。"""

    def __init__(
        self,
        engine: Engine,
        embedder: Embedder,
        store: VectorStore,
        *,  # 默认 top_k 由 QueryKbInput 携带（1–10，schema 冻结）
        min_score: float = 0.01,
    ) -> None:
        self._engine = engine
        self._embedder = embedder
        self._store = store
        self._min_score = min_score

    def search(self, query: str, top_k: int, *, session: Session | None = None) -> list[KbHit]:
        """检索 active 块（superseded 索引清除 + 权威表双保险）；hit_count++（D-57 真实召回计）。"""
        own_session = session is None
        db = session or Session(self._engine)
        try:
            vector = self._embedder.embed([query])[0]
            raw_hits = self._store.query(vector, top_k)
            hits: list[KbHit] = []
            for row in raw_hits:
                if row.get("score", 0.0) < self._min_score:
                    continue
                chunk = db.get(KbChunk, int(row["id"]))
                if chunk is None or chunk.superseded_at is not None:
                    continue  # 索引脏窗口兜底：权威表已淘汰即不召回
                hits.append(
                    KbHit(
                        chunk_id=chunk.id,
                        incident_id=chunk.incident_id,
                        section=chunk.section,
                        text=chunk.text,
                        score=float(row["score"]),
                    )
                )
                chunk.hit_count += 1
            if hits:
                db.commit()
            return hits
        finally:
            if own_session:
                db.close()


def build_query_kb_handler(
    retriever: KbRetriever | None,
) -> Callable[[BaseModel], ToolResult] | None:
    """query_kb handler 工厂：retriever 未装配 → None（registry 落 stub，零回退）。

    ToolResult 形状照 D-23 冻结：ok = data 携带 kb_hits[]；empty = 无命中；
    异常在 handler 内兜底为 error（工具层永不向上抛原始异常，D-16）。
    """
    if retriever is None:
        return None

    def handler(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
        try:
            query = args.query
            top_k = args.top_k
            if timeout_seconds <= 0:
                raise ToolTimeoutError("query_kb 超时预算非法")
            hits = retriever.search(query, top_k)
        except ToolTimeoutError:
            raise
        except Exception as exc:  # D-16 兜底：检索失败 ≠ 调查失败
            return ToolResult(
                tool="query_kb",
                status=ToolStatus.ERROR,
                data=None,
                meta={"error_class": type(exc).__name__, "reason": f"知识库检索失败：{exc}"},
            )
        if not hits:
            return ToolResult(
                tool="query_kb",
                status=ToolStatus.EMPTY,
                data=None,
                meta={"reason": "知识库无相似历史案例", "source": "kb"},
            )
        return ToolResult(
            tool="query_kb",
            status=ToolStatus.OK,
            data={"kb_hits": [h.as_dict() for h in hits]},
            meta={"source": "kb", "note": "历史相似案例（参考），非当前事实"},
        )

    return handler
