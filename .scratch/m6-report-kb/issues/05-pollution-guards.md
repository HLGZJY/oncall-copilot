Status: resolved
Blocked by: 04

# pollution guards

## 任务

三道防线机械断言：①入库门槛拒绝测试细化（investigating/escalated/failed 全拒）；②Verifier 规则层约束——仅 kb 支撑的假设证实被拒并提示补本源证据（kb 证据单独计数，D-25/D-26 接缝扩展）；③覆盖淘汰联动测试（重调查覆盖 → 旧块 superseded → 召回不再命中）；hit_count 只计真实向量召回不计缓存复用（D-57）

## 验收（可机械判定）

- [x] pytest 绿：三道防线各独立断言；污染场景 e2e（错误结论不回流：未实证调查的报告不出现在召回结果）
