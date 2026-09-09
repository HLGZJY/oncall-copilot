"""Embedding 封装（D-51/G3）：Protocol 可注换——测试用 MockEmbedder，真实模型可后挂。

设计期零真实调用纪律：`SentenceEmbedder` 只在显式注入路径实例化（真实依赖
sentence-transformers 随 T3 依赖冒烟票落位），本模块 import 零三方重依赖。
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

__all__ = ["Embedder", "MockEmbedder"]


@runtime_checkable
class Embedder(Protocol):
    """embedding 接缝：文本 → 定长向量（确定性，同文本同向量）。"""

    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class MockEmbedder:
    """确定性假向量（测试与 CI 全走此路径，零真实调用）。

    形态 = sha256 派生的 `dimension` 维向量（同文本恒同向量、异文本几乎必然
    异向量）——只供相似度检索接缝的机械断言，不承载语义质量（语义质量归 T7
    真实模型实测回填）。
    """

    def __init__(self, dimension: int = 32) -> None:
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:  # noqa: D102（见类 docstring）
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # 摘要字节循环铺满维度，归一到 [-1, 1]
        return [(digest[i % len(digest)] / 127.5) - 1.0 for i in range(self.dimension)]
