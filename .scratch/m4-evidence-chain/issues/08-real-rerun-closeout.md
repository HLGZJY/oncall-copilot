Status: ready-for-agent
Blocked by: 05, 06, 07

# 08 真实实测重跑与收尾（T8；真实调用开工前需用户确认 key）

> **开工门槛（照 M3 issue 08 流程）**：真实 LLM 调用前需用户确认 key（`ONCALL_LLM_*` env / `~/.oncall-llm-env` 三件套）并授权预算（M3 实测口径：两轮 6 次合计 ≈¥0.008，单次最高 ¥0.004，R9 单价，来源 issue 08 注记 2026-09-08）。

## 任务

- **两缺口修复后重跑真实实测**：3 剧本（cpu-spike / slow-sql / queue-backlog）真实调用（qwen3.7-flash，JSON Mode），回填步数/耗时/成本/结论
- **Top-1 如实记录**（禁虚构；口径复核归 M7）——与 M3 基线（首版 0/3，主失败 = 参数级失败 + 同参绕圈）对比：opening 视图（05）与 schema 摘要（06）修复后的改善幅度是本票核心观测
- 参数级失败率与畸形率（JSON 契约）对比 M3 基线记录；usage 明细落 `.scratch/tmp/`（不进版本库）
- **收尾**：设计文档验收节逐条回填 → 翻 `implemented`；`CONTEXT.md`/`decisions.md` 落位核对；spec 状态节更新；架构 §4 六表→七表回写与 agent-loop-design 修订（03 已带）落位终核

## 要点

- 真实调用零改测试：`MockPlanner` → `OpenAIPlannerClient` 热切换，契约测试不变（M3 先例）；证据取 golden timeline 同源 Fetcher 替身（D-18，不含 root_cause/investigation_path/remediation）
- 落库链路在真实轮全程生效——真实轮即「100% 落库」的实战验证
- 成本逼近 ¥0.5 上限即停手上报（实测基线远低于该值，预期不触发）

## 验收（可机械判定）

- [ ] 3 剧本真实实测回填完整：步数/耗时/成本/Top-1/参数级失败率/畸形率（逐剧本记录，禁虚构）
- [ ] 与 M3 基线对比结论落 Comments（改善或未改善均如实记录；未改善须分析归因并交用户复议）
- [ ] 设计文档验收节全部打勾、翻 `implemented`；CONTEXT/decisions 落位核对完成
- [ ] 全量 pytest + ruff 双检绿；coverage ≥80%
