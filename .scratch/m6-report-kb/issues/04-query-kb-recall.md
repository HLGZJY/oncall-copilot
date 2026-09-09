Status: resolved
Blocked by: 03

# query kb recall

## 任务

kb/retriever.py（QueryKbInput → 向量检索 → kb_hits[] 含 incident_id/section/text/score/source=kb）；registry handler 注入接缝（照 execute_action 先例，Protocol/TYPE_CHECKING，C3 不破；未注入时 query_kb 维持 unavailable stub 零回退）；kb/recall.py（开局召回：指纹精确命中 + 源 mitigated → 缓存复用出口 reused_from 不重查（D-57）；未命中 → 向量相似 → kb 参考证据节点随 opening 注入，不占步数预算）；POST /investigate 行为扩展（契约不破）

## 验收（可机械判定）

- [x] pytest 绿：query_kb 注入后返回 ok + kb_hits、未注入维持 unavailable；缓存复用/向量参考两路径互斥断言；开局节点不占步数；D-23 冻结面（六工具集合/入参/ToolResult 形状）断言不变
