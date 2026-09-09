Status: resolved
Blocked by: 05

# 06 恢复验证 + 回滚（T6 / G6 / G7 / D-44 / D-45）

## 任务

- 新建 `src/oncall/remediation/verifier.py`（行数预算 ≈120）：**恢复验证器**——runbook `verification` PromQL 回查判恢复（复用 `context/promql` Fetcher 接缝注入）；恢复/未恢复机械判定
  - 判据 = runbook verification 显式声明（D-44）：`{promql, condition, window_s}`
  - cpu-spike：非 DB 路径 P95 ≤0.05s（以 runbook 定稿值为准，取剧本实测回落值）
  - slow-sql：`demo_db_pool_used` 回落池上限内 + 锚定告警 resolved 观察窗 60s（以 runbook 定稿值为准）
- 未恢复路径（D-45）：自动执行 runbook 显式定义 `rollback`（白名单原子操作序列，经 issue 05 执行器）→ 仍失败 → **转人工**（proposal 落 `escalated`、incident 保持 investigating——D-28 转人工不是丢弃）
- 恢复路径：proposal 收尾 `recovered` + incident 翻 `mitigated`（既有枚举流转，D-19/D-46）

## 要点

- 验证结果落 `verify_result_json`（promql 结果 + condition 判定 + 时间窗）供 M7 处置成功率矩阵
- 回滚同样过白名单 + 留痕（D-45：rollback 引用白名单原子操作，与 actions 同执行面）
- **不做逆操作推导**（D-45）——rollback 只来自 runbook 显式定义
- Fetcher 注入替身断言两条路径（恢复/未恢复→回滚→转人工）；不依赖活 Prometheus（活栈真实验证留 issue 08）
- 领域名：恢复判据 / 恢复验证 / 转人工（CONTEXT.md 权威词）

## 验收（可机械判定）

- [x] pytest 绿：恢复路径——Fetcher 替身返回「判据满足」→ proposal 收尾 `recovered` + incident 翻 `mitigated`（test_remediation_verifier.py + test_remediation_api.py::test_approve_recovered_flips_incident_mitigated）
- [x] pytest 绿：未恢复路径——Fetcher 替身返回「判据不满足」→ 自动执行 rollback（替身断言 rollback 命令过白名单执行器）→ 回滚后复验仍失败 → proposal 落 `escalated` + incident 保持 `investigating`（test_not_recovered_rollback_still_fails_lands_escalated + api 级 test_approve_not_recovered_with_rollback_escalates_after_reverify）
- [x] pytest 绿：回滚成功后复验通过 → proposal 收尾 `recovered`（rollback_status 落 `rolled_back` + recovered，状态机定稿见落位注记）
- [x] pytest 绿：`verify_result_json` 字段全落（promql 结果/condition 判定/window；另含 observed 最劣样本 + samples 计数）
- [x] pytest 绿：rollback 命令白名单校验失败 → 不执行 + 审计 + 转人工（test_whitelist_rejection_blocks_rollback_and_audits：`$session_id` 未注入 → rejected 留痕 → escalated + rollback_status=`blocked`，不复验）
- [x] 全量门禁不回退（基线修正为 **649/10**——票面原写 541/10 是 M3 末期旧值）+ ruff 双检：实测 **667 passed / 10 skipped**（649 基线 + 本票 18 新增），ruff check / ruff format --check / import-linter C3–C6 / bandit / 架构守卫全绿

## 落位注记（issue 06 实现票回填，2026-09-09）

- **落点**：`RunbookRecoveryVerifier` + 回滚编排 `run_confirm_chain` 同文件落 `src/oncall/remediation/verifier.py`（216 行）。编排不落 service.py 的原因：service.py 已 278 行逼近 C6 ≤300 上限；api 层 confirm 链只换调用点（D-40 裁决④），一行状态逻辑未重写。
- **verifier 判定形状**（verify 返回 dict，落 `verify_result_json`）：
  `{"recovered": bool, "promql": str, "condition": str, "window_s": int, "observed": float, "samples": int}`；
  异常路径（未知 runbook / condition 不可解析 / 无样本 / 回查失败）追加 `"error": str` 且 recovered=False（fail-closed，不静默放行）。observed = 观察窗内最劣样本（最大观测值）。
- **condition 机械判定形状**（本票定稿）：`<观测名> <op> (<数值> | <参考名>)`，op ∈ {<=,>=,==,<,>}；观测名 = promql 回查结果，参考名 = verifier 构造期注入 `reference_values`（slow-sql：`{"db_pool_size": <池上限>}`）；观察窗内**全部样本**满足判据才判恢复（判据需持续回落）。任一侧解析不了 → fail-closed 未恢复。
- **rollback_status 取值**（本票定稿）：`skipped`（rollback=[] 或 runbook 缺失，未恢复直边 escalated）/ `rolled_back`（回滚已执行；复验通过 → status=recovered，复验仍失败 → status=escalated）/ `blocked`（回滚被白名单拒绝未执行，审计留痕后 escalated 兜底，不复验）。白名单拒绝判定 = 执行器返回 `executed` 为空。
- **观察窗语义**：query_range 一次性拉取窗口快照（start=now-window_s, end=now, step=15s），**不真实 sleep**；真实窗口采样与逐点观察归 issue 08。
- **api 层改动范围**：`RemediationDeps` 增 `runbooks` 字段（回滚来源，None 兼容旧装配）；confirm approve 同步链换成 `run_confirm_chain` 单调用点；D-48 降级路径不变。语义变化一处：未恢复落点由 issue 04 的 `failed` 改为 D-28 的 `escalated` 分叉（本票设计使然），对应 test_remediation_api.py 的 not-recovered 用例已改名并按新契约断言；StubExecutor 形状对齐 issue 05 审计返回 `{executed, rejected, ok, output_summary}`。
- **incident 翻转**：recovered → `mitigated`（orchestrator 内直接模型更新，api 层不查表）；escalated/rolled_back 路径 incident 保持 `investigating`（D-28/D-45）。
- **CONTEXT.md 新词**：回滚编排 / Rollback Orchestration、回滚 / Rollback（含 _Avoid_：逆操作推导）。
- **decisions.md**：零新增 D（D-44/D-45/D-28/D-40 按设计消费）。
