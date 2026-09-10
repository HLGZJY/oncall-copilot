Status: ready-for-agent
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
