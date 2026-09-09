Status: needs-triage
Blocked by: 03

# 04 指标核算（五列 + 复用/escalated 单列）

## 任务

- `metrics.py`：Top-1/Top-3 命中、步数/耗时/成本（均值±极差）、失败模式归类规则链（六值直消费 → 规则兜底 → unknown 强制人工通道禁丢弃）
- 复用出口不计入命中分母、复用命中率单列（D-57）；escalated 不计失败、单列（D-56）
- 降噪指标直接复用 `classify/stats.compute_denoise_metrics`（M2 issue 05，勿重造）

## 验收

- [ ] 指标核算单测绿（构造已知输入断言已知输出；R=0 不除零先例同款）
- [ ] unknown 落人工通道有记录（禁丢弃）单测绿
- [ ] 全量 pytest 只增不减 + ruff 双检绿

## Comments

- 设计引用：G3/G6/G7 + R6
