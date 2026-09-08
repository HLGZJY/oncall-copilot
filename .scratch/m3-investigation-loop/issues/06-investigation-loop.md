Status: resolved
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

- [x] **防伪三判据三组单测绿（G8）**：① 同一 incident 注入两个不同 MockPlanner 脚本 → 步序列与结论随脚本改变；② 全程不调 get_topology 亦正常收束；③ 假设被推翻→换假设 / 工具失败重试 ≤2→Fallback / 第 15 步强制 escalated
- [x] 失败模式六值各 ≥1 个触发用例绿（mock 编排逐值触发，归类字段精确断言）
- [x] 终止三出口单测绿（conclusion 收束 / 15 步 escalated / 绕圈熔断）
- [x] 防绕圈四机制单测绿（重复检测计数与熔断 / 假设去重拒绝 / Fallback 换向由脚本驱动）
- [x] EvidenceStep 100% 记录断言绿（每步含 output_json + output_summary）
- [x] 全量 pytest + ruff 双检绿

## 落地注记（2026-09-08，T6 收尾）

- **落位**：`src/oncall/harness/loop.py`（297 行，C6 顶格内）+ `tests/unit/test_harness_loop.py`（19 用例）+ `tests/unit/test_harness_loop_mechanisms.py`（14 用例）；门禁实测 **450 passed / 4 skipped（基线 423 只增不减）· coverage 97.59% · ruff check + format 绿**
- **入口签名偏差**：票面「run_investigation(session, planner, registry, gate, verifier, context, ...)」7 参超 PLR0913（max-args=5），按踩坑②收拢为 `run_investigation(session, components, *, max_steps=MAX_STEPS)`，组件经 `LoopComponents` dataclass 注入——可注入性（G8 判据 1 前提）不受影响
- **假设文本来源**：D-22 协议冻结（`PlannerDecision` 无 hypothesis 字段），假设文本取自决策输出 `thought`（协议唯一 prose 字段）；去重用规范化文本包含匹配（纯规则）
- **归类判据细化**（机械可观测状态）：`tool_error` = 工具重试耗尽（registry meta `failure_mode=tool_error`）后未换向、走到 15 步 escalate 时归类；`no_signal` > `premature_stop` 判定优先级；正常收束（≥5 步或有 confirmed）`failure_mode=None`；hallucination（零证据收束/臆测引用）走 `abort` 终态
- **重复调用检测**：规范化哈希 = args 排序 JSON（`canonical_args` 纯函数）；连续 ≥2 或累计 ≥3 注入循环警告，紧随再犯熔断 plan_error（熔断发生在执行前，不计步）
- **权限拒绝/未知工具/入参不合法**：不落证据步（未执行）、留 gate 审计或 Fallback 摘要喂回，复用重复检测兜底绕圈
- **C6 顶格压缩**：loop.py 297 行贴上限——模板落模块级常量、私有函数 docstring 精简、`__all__` 移除（format 强制展开会顶爆行数）；后续若加功能需先拆模块
