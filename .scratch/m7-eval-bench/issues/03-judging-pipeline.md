Status: resolved
Blocked by: 01

# 03 判对错管线（规则匹配层 + judge 契约 mock 冻结）

## 任务

- `judging.py`：规则匹配层（根因关键词比对 + `normalize_hypothesis_text` 规范化包含判定，命中即 top1/top3）
- judge 契约（Protocol：输入=结论+证据摘要+黄金标注，输出=`{verdict, reason}`）**先冻结 mock 实现**；真实 judge 走 `ONCALL_LLM_*` 独立 env 配置，是 key 门槛票（D-50 先例），本票零真实调用
- judge 模型与被评模型解耦（防自评）

## 验收

- [ ] 规则匹配层单测绿（命中/未命中/规范化边界）
- [ ] judge mock 契约测试绿（畸形输出 → LLMOutputError 族逐字对齐 D-07）
- [ ] 全量 pytest 只增不减 + ruff 双检绿

## Comments

- 设计引用：G2/R4/R5
