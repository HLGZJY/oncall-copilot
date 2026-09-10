Status: resolved
Blocked by: M3 issue 09（已 resolved，91c6373）

# 10 全量重跑前置：小批稳定性验证 + 单价 env + 预算核定（M7 issue 07 重跑闸门）

## 任务

- **小批验证**：真实档 3 剧本（含 ≥1 难剧本，如 db-deadlock / pool-exhaustion）× 2 被评模型 × 1 遍，实测修复后熔断/plan_error 率（对照 issue 09 报告的修复前 3/6 熔断基线）；**非空结论 ≥ 4/6 且 0 VerifierError → 放行全量**，否则停下上报；
- **单价 env**：百炼各档 `ONCALL_LLM_PROFILE_<NAME>_PRICE_IN/OUT` 配齐（缺失则 cost 列全 0，违反验收「三列有数字」）；judge 档 `ONCALL_JUDGE_LLM_*` 切 deepseek-v4-flash-0731 并配单价；
- **预算核定（硬闸门）**：以小批实测 tokens 外推全量消耗，**逐模型对照余量**（qwen3.7-flash 余 859,041；qwen3.8-max 余 1,000,000；deepseek-v4-flash-0731 余 225,559 仅 judge）。issue 09 冒烟实测 ~11,835 tokens/次（6 步简单剧本）→ 72 次全量 ≈ 864K，**已超 qwen3.7-flash 余量**——外推后给出「N=3 可行 / 需降 N=2」的实测结论与推荐矩阵，**超余量的方案禁止开跑**，停下交用户拍板；
- 全程 ONCALL_RUN_M7_EVAL=1 解锁路径，跑前 `git push` 确认远端同步（502 则如实记录不阻塞）。

## 模型档位（沿用 issue 09 拍板）

| 角色 | 模型 | 余量（tokens） |
|---|---|---|
| 被评·基线 | qwen3.7-flash | 859,041 / 1,000,000 |
| 被评·旗舰 | qwen3.8-max | 1,000,000 / 1,000,000 |
| judge | deepseek-v4-flash-0731 | 225,559 / 1,000,000 |

端点/key：`C:\Users\heguo\.oncall-llm-env`；冒烟脚本 `C:\Users\heguo\oc-llm-smoke.py`（用项目 venv 解释器跑）。

## 验收（可机械判定）

- [ ] 单价 env 配齐，小批 6 格 cost 列全部有数字（非 0/空）
- [ ] 小批实测行落 eval_runs 可回溯；熔断/plan_error/VerifierError 计数回填本 Comments
- [ ] 预算外推表（每模型：外推全量 tokens vs 余量 vs 推荐矩阵）回填本 Comments，交用户拍板后才可开全量
- [ ] 全量 pytest 只增不减（基线 833）+ ruff 双检 0 错（涉码改动时）

## Comments

- 2026-09-10 建票。前置核对：M3 issue 09 已 resolved（91c6373 在列，HEAD 5f9faba）；
  pytest collect 833（基线持平）+ ruff check / format 双绿（本票零涉码改动）。
- **①单价 env 配齐（`.env`，key 不入 git；牌价均有公开信源，取价 2026-09-10）**：
  - qwen3.7-flash：0.24 / 0.96 ¥/M（PAI《Token 服务计费说明》华北2 北京，沿用 M2 issue 07 口径）；
  - qwen3.8-max：14.4 / 43.2 ¥/M（阿里云官方定价页华北2(北京) 0<Token≤1M 档，
    help.aliyun.com/document_detail/3025955）；
  - judge 档 `ONCALL_JUDGE_LLM_*` 切 deepseek-v4-flash-0731（百炼端点同源）：1 / 2 ¥/M
    （百炼产品动态 2026-08-03 上线公告：输入 ¥1 / 输出 ¥2）。
  - **小批 6 格 `usage_real.cost_cny` 全部有数字**：¥0.003486 / 0.006446 / 0.006316
    （qwen37-flash）+ ¥0.239861 / 0.246917 / 0.445032（qwen38-max）；judge 批级
    ¥0.007694。注：`eval_runs.cost_cny` 列为工具步估算口径（工具执行无 LLM 消耗恒 0，
    registry 埋点设计如此），成本数字以 `run_json.usage_real.cost_cny` 实测列为准。
- **②小批稳定性验证（2026-09-10 实测，3 剧本 × 2 模型 × 1 遍 = 6 格；
  eval_runs id 4756–4761 可回溯；产物 `datasets/eval/small-batch-20260910/`）**：

  | id | 剧本 | 模型 | verdict | judged_by | 非空结论 | 失败模式 | 步数 | tokens | 延迟 | cost |
  |---|---|---|---|---|---|---|---|---|---|---|
  | 4756 | cpu-spike | qwen37-flash | top3 | judge | ✘(假设非空) | plan_error | 6 | 11,520 | 16.4s | ¥0.003486 |
  | 4757 | db-deadlock | qwen37-flash | miss | judge | ✘ | plan_error | 9 | 22,415 | 21.6s | ¥0.006446 |
  | 4758 | pool-exhaustion | qwen37-flash | **top1** | judge | ✔ | - | 9 | 21,389 | 26.3s | ¥0.006316 |
  | 4759 | cpu-spike | qwen38-max | **top1** | judge | ✔ | - | 7 | 13,833 | 26.4s | ¥0.239861 |
  | 4760 | db-deadlock | qwen38-max | miss | judge | ✔(根因错判 slow-sql) | unknown | 6 | 14,933 | 27.8s | ¥0.246917 |
  | 4761 | pool-exhaustion | qwen38-max | miss | judge | ✔(根因错判 cache-avalanche) | unknown | 11 | 27,507 | 36.9s | ¥0.445032 |

  - **熔断（15 步）0 / VerifierError 0 / timeout 0 / plan_error 2/6**（4756/4757，
    均仍产出判定与 confirmed 假设，未炸批）——对照修复前 6 次冒烟 3 次熔断基线，
    行为方差显著收敛；**非空结论 4/6 ≥ 4/6 ✔ 且 0 VerifierError ✔ → 稳定性闸门放行**；
  - judge 6 次触发全部成功（deepseek-v4-flash-0731，批级 6,160 tokens / 23.9s / ¥0.0077）。
- **③预算外推与推荐矩阵（以小批实测 tokens 外推，硬规 10 禁虚构）**：

  | 模型 | 小批 tokens/次（3 格实测） | 均值 | N=3 外推（12×3=36 次） | 最坏外推（36×max） | 余量 | 结论 |
  |---|---|---|---|---|---|---|
  | qwen3.7-flash | 11,520 / 22,415 / 21,389 | 18,441 | ≈663,900 | 806,940 | 859,041 | **N=3 可行**（余 22.7%；最坏仍不超） |
  | qwen3.8-max | 13,833 / 14,933 / 27,507 | 18,758 | ≈675,300 | 990,240 | 1,000,000 | **N=3 可行**（余 32.5%；最坏贴边 99.0%） |
  | deepseek-v4-flash-0731 (judge) | 6,160 / 6 格 ≈1,027/格 | — | 72 格 ≈73,900 | — | 225,559 | **充裕**（余 67%） |

  - 外推成本（N=3 双模型全量）：qwen37-flash ≈¥0.20 + qwen38-max ≈¥11.19 + judge ≈¥0.09
    ≈ **¥11.5**，远低于 ¥36 硬顶（issue 07 拍板口径）；
  - **推荐矩阵**：A）N=3 双模型全量 → 可行（推荐，注意 qwen3.8-max 最坏情形余量仅 ~1%，
    建议开「用完即停」+ 逐批 flush 落库 + 按批核对余量）；B）N=2 双模型（48 次）→
    qwen37 ≈442K（余 48%）/ qwen38 ≈450K（余 55%），安全边际方案；
    C）超余量方案：无（两被评档 N=3 均未超）；
  - 风险注记：小批仅 3 剧本、token/次方差 ~2.4×，dev 全集 12 剧本含更难样本，
    外推按均值口径、最坏口径亦 < 余量，但 qwen3.8-max 最坏贴边属已知风险。
- **④放行结论（实测）**：稳定性闸门 ✔（非空结论 4/6 + 0 VerifierError + 0 熔断）；
  单价 env ✔（6 格 cost 全有数字）；预算核定 ✔（N=3 双模型均不超余量）。
  **三闸门全过，推荐方案 A（N=3 双模型全量）交用户拍板后开跑**——全量重跑本身不在
  本票范围，另票派发。
