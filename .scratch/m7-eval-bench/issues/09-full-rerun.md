Status: ready-for-agent
Blocked by: M7 issue 08（已 resolved，三闸门全过 + 方案 A 拍板）

# 09 真实档 dev 全量重跑（N=3 双模型，修复后正式矩阵）

## 任务

- 全量 12 dev 剧本 × qwen37-flash + qwen38-max × N=3（72 格），`run_eval_real(n_runs=3)`；
- **用完即停保护**：逐批（每 12 格）落库核对 `usage_real` 累计 tokens，qwen38-max 累计逼近
  950K（余 <5%）或 qwen37-flash 逼近 820K 即停批上报，不许烧穿余量；
- 完成后双产物落 `datasets/eval/` + 矩阵报告（质量/成本/延迟三列数字，cost 以
  `run_json.usage_real.cost_cny` 为准）；judge（deepseek-v4-flash-0731）批级聚合；
- 修复前后对比注记：与 issue 07 批3（k2-6/k3，72 格全 miss）同口径对比表回填 issue Comments。

## 验收（可机械判定）

- [ ] 72 格全部落 eval_runs 可回溯，failure_mode 100% 归类（无 unknown 除错判注记外）
- [ ] 双产物落盘，三列数字齐全（禁虚构，全部实测）
- [ ] 修复前后对比表回填 issue Comments（Top-1/Top-3/熔断率/cost 四指标）
- [ ] 中途 stop-loss 未触发则跑满 72 格；触发则已完格保留 + 上报
- [ ] pytest/ruff 门禁绿（若涉码）；commit 中文 type 前缀，实测数字进 body
