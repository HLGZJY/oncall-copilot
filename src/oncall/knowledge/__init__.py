"""knowledge：报告与知识库（闭环报告 + RAG 历史召回，M6）。

C3 禁列（pyproject 预留位）：`oncall.knowledge` 禁止被 harness import——
query_kb 走 registry 既有 handler 注入接缝（Protocol/TYPE_CHECKING），
召回/入库只被 api 层与知识模块内部消费（照 `oncall.remediation` 先例）。

知识污染三道防线（D-49–D-57）：①入库门槛 = incident `mitigated` 实证；
②kb 证据语义为「参考」，不可独立证实假设（Verifier 规则层约束）；
③重复调查覆盖时旧块同步 `superseded_at` 淘汰。
"""
