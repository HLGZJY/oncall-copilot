"""向量索引接缝（D-52/G4 + D-55/G7）：SQLite `kb_chunks` 权威，向量库只作可重建索引。

`VectorStore` Protocol 两个实现：
- `InMemoryVectorStore`：余弦相似内存实现（测试/CI 默认，零依赖）；
- `ChromaVectorStore`：Chroma persistent lazy-import——未安装 chromadb 时
  构造抛 `VectorStoreUnavailable`（unavailable 语义对齐 D-16/D-23：依赖缺失
  ≠ 功能失败，调用方回落 InMemory 或提示），import 不炸。
"""

from __future__ import annotations

import math
from typing import Any, Protocol, runtime_checkable

__all__ = ["ChromaVectorStore", "InMemoryVectorStore", "VectorStore", "VectorStoreUnavailable"]


class VectorStoreUnavailable(RuntimeError):
    """向量索引后端依赖缺失/不可用（unavailable 语义，D-16 对齐）。"""


@runtime_checkable
class VectorStore(Protocol):
    """向量索引接缝：upsert / 按 incident 覆盖删除 / top_k 相似检索。"""

    def upsert(
        self, ids: list[str], vectors: list[list[float]], metadatas: list[dict[str, Any]]
    ) -> None: ...

    def delete_incident(self, incident_id: int) -> None: ...

    def query(self, vector: list[float], top_k: int) -> list[dict[str, Any]]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class InMemoryVectorStore:
    """余弦相似内存索引（kb_chunks 行 id 即向量 id；可由权威表全量重建）。"""

    def __init__(self) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._metadatas: dict[str, dict[str, Any]] = {}

    def upsert(
        self, ids: list[str], vectors: list[list[float]], metadatas: list[dict[str, Any]]
    ) -> None:
        for i, vec, meta in zip(ids, vectors, metadatas, strict=True):
            self._vectors[i] = list(vec)
            self._metadatas[i] = dict(meta)

    def delete_incident(self, incident_id: int) -> None:
        stale = [i for i, m in self._metadatas.items() if m.get("incident_id") == incident_id]
        for i in stale:
            self._vectors.pop(i, None)
            self._metadatas.pop(i, None)

    def query(self, vector: list[float], top_k: int) -> list[dict[str, Any]]:
        scored = ((i, _cosine(vector, vec)) for i, vec in self._vectors.items())
        ranked = sorted(scored, key=lambda pair: pair[1], reverse=True)[:top_k]
        return [
            {"id": i, "score": round(score, 6), **self._metadatas[i]}
            for i, score in ranked
            if score > 0.0
        ]


class ChromaVectorStore:
    """Chroma persistent 懒加载实现（真实依赖就绪后注换；未安装即 unavailable）。"""

    def __init__(self, path: str, collection: str = "kb_chunks") -> None:
        try:
            # lazy import：未安装不炸模块 import（unavailable 语义，D-16）
            import chromadb  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - 依赖冒烟票负责安装
            raise VectorStoreUnavailable(
                "chromadb 未安装：向量索引回落 InMemoryVectorStore（权威在 kb_chunks 表）"
            ) from exc
        self._client = chromadb.PersistentClient(path=path)
        self._collection = self._client.get_or_create_collection(collection)

    def upsert(
        self, ids: list[str], vectors: list[list[float]], metadatas: list[dict[str, Any]]
    ) -> None:
        self._collection.upsert(ids=ids, embeddings=vectors, metadatas=metadatas)

    def delete_incident(self, incident_id: int) -> None:
        self._collection.delete(where={"incident_id": incident_id})

    def query(self, vector: list[float], top_k: int) -> list[dict[str, Any]]:
        result = self._collection.query(query_embeddings=[vector], n_results=top_k)
        hits: list[dict[str, Any]] = []
        ids = result.get("ids", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        for i, meta, dist in zip(ids, metas, distances, strict=False):
            hits.append({"id": i, "score": round(1.0 - float(dist), 6), **meta})
        return hits
