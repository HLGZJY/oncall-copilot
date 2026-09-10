Status: ready-for-agent
Blocked by: M7 issue 09（已 resolved；Top-1 卡 25% 的主要拖累 = 难剧本全 miss）

# 10 难剧本取证面缺口：诊断 → 补齐（两段式，诊断后回 human 复核）

## 背景（实测证据）

M7 issue 09 修复后 dev 全量：5 个难剧本两档仍 0–1/3 全 miss——
db-deadlock / downstream-timeout / process-killed / slow-sql / packet-loss。
简单剧本（cpu-spike/pool-exhaustion 等）已能 top1/top3，说明策略升级有效；
难剧本 miss 主嫌疑是**取证工具深度/粒度不足**（如 db-deadlock 需锁等待视图、
slow-sql 需慢查询明细、packet-loss 需网络分层视角、process-killed 需容器事件/
ExitCode、downstream-timeout 需分位延迟分布），而非策略问题（硬规 6）。
另：qwen3.8-27b plan_error 7/33 待归类，可能同源，诊断时一并看。

## 阶段一：诊断（本票开工面，产出后 Status → ready-for-human）

对 5 个难剧本逐一产出「证据缺口清单」，每条含：

- golden `investigation_path` 要求的关键证据项（读 `datasets/golden/dev/*.yaml`）；
- 现有 6 工具（`src/oncall/harness/tools/` + `src/oncall/eval/evidence.py` golden 面）
  能否产出该项（能/不能/粒度不够）；
- 缺口归类：新工具 / 既有工具加深（返回更多维度）/ 视图注入不足；
- 结合 issue 09 的 33 格 plan_error 行级 run_json（eval_runs 可 SQL 回溯）看
  planner 在难剧本实际卡在哪一步（绕圈/空证据/畸形输出）。

诊断产物落 `docs/design/` 下诊断笔记（引用 eval_runs 行号，禁虚构），并在
issue Comments 摘要 → **停下等用户复核拍板补齐方案**，不得直接开工阶段二。

## 阶段二：补齐（用户拍板后，Status 改回 ready-for-agent）

- 按拍板清单逐项实现（新工具/加深/视图注入），TDD 红绿；
- 每补一项跑对应难剧本真实档单格冒烟（qwen3.8-27b 性价比档即可），验证
  planner 能拿到该证据且 0 VerifierError；
- 全部补齐后：5 难剧本 × 27b × 1 遍小批回归（预算：27b 池 + judge 余量，
  跑前核对 deepseek judge 池剩余 ≈92.7K 是否够，不够先上报扩容）；
- 更新 CONTEXT.md 新工具/新证据项词条。

## 验收（可机械判定）

- [ ] 阶段一：5 剧本缺口清单落 docs/，每条有 eval_runs 行号或 golden 字段引用
- [ ] 阶段二：小批回归 5 剧本 ≥3 个产出非空结论且 0 VerifierError（数字回填 Comments）
- [ ] 全量 pytest 只增不减（基线 833）+ ruff 双检 0 错
- [ ] 真实调用只在 ONCALL_RUN_M7_EVAL=1 路径；stop-loss：单格 tokens >60K 即停
