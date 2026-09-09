Status: resolved
Blocked by: 01

# chunking embedding pipeline

## 任务

kb/chunking.py（五类 section 章节切块，块带 incident_id/section/seq）；kb/embedder.py（Embedder Protocol + MockEmbedder 确定性向量 + 真实模型可注换）；kb/store.py（VectorStore Protocol + 内存实现 + Chroma lazy-import 可选实现——未安装时 unavailable 语义，不炸 import）；kb/pipeline.py（入库编排：D-56 门槛 incident==mitigated 才入库，否则拒绝；investigations 覆盖时 kb 旧块同步 superseded_at）；sentence-transformers/chromadb 依赖冒烟（装包结果如实记录，装不上不阻塞——Mock 路径全绿为准）

## 验收（可机械判定）

- [x] pytest 绿：切块边界/数量/元数据断言；MockEmbedder 确定性（同文本同向量）；入库门槛拒绝未实证事件；覆盖同步 superseded；ruff/bandit 全绿
