Status: ready-for-agent
Blocked by: 01, 02, 03, 04, 05

# 06 主循环 Loop：终止语义与失败归类（T6 / G6·G7·G8 定案 = D-27·D-28）

> **就绪态终审**：G6/G7 已于 2026-09-08 定案（用户采纳推荐解，登记 D-27/D-28），本票转 `ready-for-agent`；票面即定案口径（G8 测试策略照设计文档 §开放设计点 G8 行执行）。

## 任务（D-27/D-28 定案口径）

主循环落 `src/oncall/harness/loop.py`（照架构 §3.1 骨架，推理与权限分离在不同代码路径）：

- **每步推进**：Planner 决策 → PermissionGate 校验 → ToolRegistry 执行 → session.record（EvidenceStep 100% 记录）→ ContextManager 更新窗口 → Verifier 校验 → 步数检查
- **终止三出口（G7）**：① Planner `{conclusion}`（正常收束）；② 步数 = 15 → `escalate_to_human`（session.status=escalated，incident 保持 investigating）；③ Harness 熔断（绕圈触发 / 总时长 >5min）
- **失败模式六值归类**：`tool_error` / `plan_error` / `timeout` / `hallucination` / `no_signal` / `premature_stop`（步数 <5 且无 confirmed 假设即收束——预标注，M7 复核）判定规则落 `InvestigationResult.failure_mode`
- **防绕圈四机制（G6）**：重复调用检测（同 tool+args 规范化哈希，连续 ≥2 或累计 ≥3 → 注入警告 → 再犯熔断）；假设去重（规范化相似拒绝入池）；步数 ≥10 移出证伪假设（调用 T4）；Fallback（失败摘要喂回 Planner 自行换向，Harness 不硬编码换向顺序）

## 要点

- **MAX_STEPS = 15 硬编码于 Harness**，不进 prompt（架构 §3.4 防失控三闸之一）
- 循环体零业务判断（架构 §3.2：Loop 不做业务判断）；决策输入可注入替换（防伪判据 1 的两脚本对照测试前提）
- escalation 无 UI 落点 = escalated 状态 + 已取证证据链可导出（T7 API 查）
- ruff PLR0912 分支 ≤12、函数 ≤50 语句：循环体按组件拆私有函数

## 验收（可机械判定）

- [ ] **防伪三判据三组单测绿（G8）**：① 同一 incident 注入两个不同 MockPlanner 脚本 → 步序列与结论随脚本改变；② 全程不调 get_topology 亦正常收束；③ 假设被推翻→换假设 / 工具失败重试 ≤2→Fallback / 第 15 步强制 escalated
- [ ] 失败模式六值各 ≥1 个触发用例绿（mock 编排逐值触发，归类字段精确断言）
- [ ] 终止三出口单测绿（conclusion 收束 / 15 步 escalated / 绕圈熔断）
- [ ] 防绕圈四机制单测绿（重复检测计数与熔断 / 假设去重拒绝 / Fallback 换向由脚本驱动）
- [ ] EvidenceStep 100% 记录断言绿（每步含 output_json + output_summary）
- [ ] 全量 pytest + ruff 双检绿
