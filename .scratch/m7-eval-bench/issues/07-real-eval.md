Status: resolved

> 评审已定（D-58–D-65），但本票附双门槛：①key 门槛票单独拍板 ②活 demo 栈 + chaos 环境就绪——开工前需用户确认。
Blocked by: 06

# 07 真实评测（key 门槛票 + 活栈环境门槛）

## 任务（附门槛，开工前需用户确认）

- **key 门槛票**：真实 LLM key（judge + 被评模型 profile）单独拍板落位（D-50 先例）
- **环境门槛**：活 demo 栈 + chaos 工具就绪；holdout 显式解锁（最终评测）
- 2 模型 × dev 集 N=3 + holdout 最终评测；实测数字回填报告与 README 挂图；成本超预算按 PRD 裁剪③（降单模型）上报

## 验收

- [ ] 实测矩阵报告产出（README 挂图），含 2 模型选型理由
- [ ] 全部数字实测回填、可回溯 eval_runs（禁虚构）
- [ ] 失败模式 100% 归类（unknown 人工通道有记录）

## Comments

- 设计引用：G2/G4/G8 验收口径 + R3（硬规 10）

- 2026-09-09 三项开工拍板：①档位 kimi-k2.6 + kimi-k3（judge=kimi-k2.7-code；两轮复核，D-66 终选）；②key 落位本地 `.env`（D-67）；③预算先冒烟外推再全量（≤¥36 硬顶）
- 2026-09-09 冒烟发现 key 现役模型仅 4 个（k2.6/k2.7-code/k2.7-code-highspeed/k3），k2.5 与 moonshot-v1 系 404 → D-66 终选；judge 真实触发验证通过
- 2026-09-09 批1 炸批复盘：M6-T5 VerifierError 穿透评测装配面 → commit 45f0e56 单格异常隔离；批2 66min 全 miss 复盘：planner 30s 超时配置过紧 + judge 快照逐行复制虚增 → 批级聚合修复 + TIMEOUT_SECONDS 90/180/60
- 2026-09-09 批3（最终，72 格，2h01m）实测：**全部 miss、0 unstable**；failure_mode：tool_error 54（VerifierError：planner 假设仅由 query_kb 支撑被知识污染防线拦截）+ plan_error 9（畸形输出重试耗尽）+ timeout 8；成本 k2-6 ¥4.49（¥0.125/次，均值 100.8s）｜k3 ¥10.16（¥0.282/次，均值 81.0s）｜judge 批级 ¥0.152（18 次）；历史累计 spend ≈ ¥24 < ¥36 硬顶
- **结论（实测）**：当前 harness 证据面 + M6-T5 防线交互下，两档被评模型均无法产出可证实结论——planner 大量引用 KB 而非仅凭取证面证据，质量差异不可分；「真实 Planner 调查视图缺事件锚点（M5 issue 08 注记）」是首要嫌疑，列为后续 harness 缺口票候选
- **待人工抽检 20%**（judged_by=judge 的 18 行全量人工复核 + judged_by=rule 随机 14 行；已留 eval_runs id 3100–3171 可回溯）：抽检结果请追加到本 Comments（未做前 Top-1/Top-3 数字按「待人工确认」口径引用）
- 验收自检：①真实档入口可达并跑通端到端 ✓（eval_runs id 3100–3171 + usage 实测）；②双产物 datasets/eval/ 落盘 + 档位结论实测回填 CONTEXT.md ✓；③全量 pytest EXIT=0（collect 820）+ ruff 双检绿 + CI 面未污染真实档 ✓
