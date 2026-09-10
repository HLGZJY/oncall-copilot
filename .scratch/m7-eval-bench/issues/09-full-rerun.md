Status: needs-info
Blocked by: 用户拍板 qwen37-flash 403 额度耗尽处置（Comments 末 A/B/C 三案）
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

## Comments

- 2026-09-10 执行记录：驱动为一次性脚本 `C:\Users\heguo\oc-m7-full-rerun.py`（不进 git），
  **逐剧本 commit（12 格/批）+ 进程内 stop-loss（qwen37>820K / qwen38>950K 停批）+
  judge 共享单例批级聚合**。为何逐剧本 commit：`run_eval_real` 原生批末才 commit，
  外部连接看不到中间行，stop-loss「逐 12 格核对落库行」无法实现——v1 全批单事务驱动
  已在启动 12 分钟后主动终止（未提交回滚，损耗 ≈¥0.03）重写。
- **⚠️ qwen3.7-flash 中途 403 免费额度耗尽——stop-loss 型停批，已完格保留上报**：
  批次 1（cache-avalanche）3 格正常；批次 2 起首轮 planner 调用即 403
  `Free quota exhausted`（PlannerError 归类 tool_error）→ 33/36 格为 error 行
  （step_count=0，单次调用 ~1.3K tokens）。**issue 08 登记的「余量 859,041」与本端点
  实际免费额度池不符**（本票 qwen37 侧仅消耗 ~122K 即触发 403，疑控制台口径/共享池
  或「用完即停」提前生效），需用户在百炼控制台核实并拍板处置（充值/关免费模式/
  换备用档 qwen3.7-flash-2026-07-15）。
  - 另注：qwen37 有效 3 格（cache-avalanche）tokens 11,537 / 28,910 / 35,982
    （均值 ≈25.5K/次）显著高于 issue 08 小批均值 18.4K——即便额度无碍，
    N=3 外推 ≈918K 也已超 859K 登记余量，issue 08 的「N=3 可行」对 qwen37 侧偏乐观。
- **qwen38-max 36/36 完整有效（本票主力结论）**：
  - **Top-1 = 9/36（25.0%）、Top-3 = 10/36（27.8%）、miss 26**——对照 issue 07 批3
    （k2-6/k3 共 72 格全 miss、Top-1 = 0）修复后质量面质变，M3 issue 09 取证策略
    升级 + 事件锚点注入被实测验证有效；
  - verdicts：top1×9（cache-avalanche 3/3 全中、cpu-spike 2、false-positive-flap 1、
    protocol-mismatch 2、slow-sql 1）+ top3×1（oom-kill）+ miss×26；
  - failure_mode 100% 归类：36 格全部 None（正常收束，无 plan_error/timeout/
    VerifierError）；步数均值 6.7、最大 15（cpu-spike run0 熔断收束 miss）；
  - 延迟：均值 26.3s/次；成本 **¥9.0188**（usage_real 实测，单价 14.4/43.2 ¥/M）；
  - tokens：**533,619**（余 1M 池剩 ≈466K）；
  - 剧本级不稳定：db-deadlock 0/3、downstream-timeout 0/3、pool-exhaustion 0/3、
    queue-backlog 0/3、process-killed 0/3、packet-loss 0/3 全 miss——难剧本取证面
    仍是缺口（与 issue 08 小批 qwen38 双难剧本 miss 一致）。
- judge（deepseek-v4-flash-0731）批级实测：**39 次调用，prompt 32,701 + completion
  12,108 = 44,809 tokens，延迟 188.9s，成本 ¥0.0569**（余 225,559 池剩 ≈180.8K）；
  72 行 judged_by=judge 全覆盖，零回退。
- 熔断率：qwen38-max 1/36（cpu-spike run0，15 步收束 miss）；qwen37-flash 有效 3 格中
  1/3（cache-avalanche run2 15 步）——对照 issue 07 批3 无从统计（全 miss 前置拦截），
  对照 issue 09 修复前冒烟 3/6 熔断，显著收敛。
- **成本合计（本票实测）**：qwen37-flash ¥0.0358（含 33 格 403 error 行浪费 ≈¥0.026）
  + qwen38-max ¥9.0188 + judge ¥0.0569 = **¥9.11**（远低于 ¥36 硬顶）。
- 双产物：`datasets/eval/full-rerun-20260910/runs-20260910.json` +
  `report-20260910.md`（矩阵表 + judge 批级用量；72 行 eval_runs id 4762–4833 可回溯）。
- **修复前后对比表（同口径，issue 07 批3 为基线）**：

  | 指标 | issue 07 批3（修复前，k2-6/k3） | issue 09 本票（修复后，qwen38-max 36 格完整） |
  |---|---|---|
  | Top-1 | 0/72（0%） | **9/36（25.0%）** |
  | Top-3 | 0/72（0%） | **10/36（27.8%）** |
  | 熔断率 | 无法统计（VerifierError 前置拦截全 miss） | 1/36（2.8%） |
  | 单格 cost（均值） | ¥0.125（k2-6）/ ¥0.282（k3） | **¥0.2504** |

- **Status: needs-info**——qwen37-flash 侧 33 格需用户拍板处置后补跑：
  A）充值/关闭「用完即停」后原档补跑 33 格（qwen37 池口径先核实）；
  B）换备用档 qwen3.7-flash-2026-07-15（issue 09 登记 510,941 余量，需实测确认）；
  C）接受 qwen38-max 单模型结论先行（Top-1 25%/Top-3 27.8% 已可引用）。
  qwen38-max 侧与 judge 侧数据完整，无需重跑。
