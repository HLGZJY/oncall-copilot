Status: resolved
Blocked by: 01

# 02 证据仓库写入接缝（T2 / G4–G5；D-33/D-34/D-25）

## 任务

- 新增 `src/oncall/db/evidence_repo.py`（行数预算 ≈150）：**步进即写**——`InvestigationSession.record_step` / `add_hypothesis` / 终态方法（conclude/escalate/abort）与 DB 写在同一点，每步一个事务（D-33）
- 落库后**行 id 回填**：`EvidenceStep`/`Hypothesis` 内存契约不倒改（D-25），行 id 在接缝层映射（session↔row 对账表），`[truncated, full at step N]` 指针接口不变
- 收尾写 `investigations` 行：终态/stop_reason/conclusion/failure_mode/step_count + **会话级成本汇总**（D-34：total_tokens/total_cost_cny = Planner `usage_log` + Verifier 裁决 usage 汇总，即 D-28 收尾结构 total 两字段；步级 tokens/cost 维持工具埋点现语义，不做步级摊销）
- 落库异常**不静默吞**：按 Harness 熔断语义冒泡归类 `tool_error`（D-33）
- escalated/aborted 会话的已取证部分同样完整落库（D-28：转人工不是丢弃）

## 要点

- C3 落位论证（设计文档「C3/C6 论证」节）：`oncall.db` 不在 harness 禁列，依赖方向 harness → db 单向合法；接缝经注入传入 loop/api，不跨包反向 import
- 步进即写语义：`record_step` 校验步号连续（M3 现契约）通过后同事务 INSERT；DB 写失败时该步不进 session（避免内存与库不一致的双头状态）
- 重复调查覆盖语义：`investigations.incident_id` 唯一冲突 → 覆盖旧行（upsert）+ 旧 evidence_steps/hypotheses 行同步清理，保证「库内 = 最近一次调查」与现注册表语义一致

## 验收（可机械判定）

- [x] pytest 绿：mock 3 步调查后 `evidence_steps` 逐行与 session.steps 逐字段一致（步进即写非批量）
- [x] pytest 绿：escalated 与 aborted 路径已取证部分完整落库、`investigations` 终态行正确
- [x] pytest 绿：行 id 回填后指针接口断言不变（D-25 契约键集合不破）
- [x] pytest 绿：落库异常注入 → 熔断归类 `tool_error`、无静默
- [x] pytest 绿：同 incident 二次调查覆盖旧行，库内只剩最近一次
- [x] 全量门禁不回退 + ruff 双检

## 回填注记（2026-09-08，T2 完成）

- 落位：新增 `src/oncall/db/evidence_repo.py`（181 行 < C6 300，预算 ≈150 内含模块级接缝函数）+ 新增 `tests/unit/test_db_evidence_repo.py`（15 测试）+ 最小接线（loop.py 297 → 299 行；api/investigation.py 164 → 166 行）
- **repo 方法粒度裁决**：`begin`（开局建 running 行 + 覆盖清理）/ `record_step`（一步一事务 INSERT）/ `add_hypothesis`（入池同步写，StrEnum 归一小写串）/ `finalize`（终态收尾写 investigations 行 + 假设裁决状态按池下标同步）；另设模块级接缝函数 `persist_step` / `persist_hypothesis`（先库后内存 + 写失败 abort 返回 False），repo 为 None 时仅写内存——既有 loop 单测零回退
- **终态写行落点裁决**：终态 `investigations` 行不在 loop 的 5 个出口点（_escalate×5/conclude/abort）逐点接线，而由 api 层在 `run_investigation` 返回后统一 `repo.finalize(result, finished_at=...)`——一处覆盖全部终态路径（含 hallucination-abort），loop.py 净增收敛到 +2 行保 C6（299 ≤ 300）；「终态与 DB 写在同一点」语义由同一请求流内紧邻调用保证，escalated/aborted 已取证部分完整落库由测试钉死
- **先库后内存（踩坑⑩）**：`persist_step`/`persist_hypothesis` 中 DB 写成功后才 `session.record_step`/`add_hypothesis`；写失败 abort 会话、该步不进 session、由 loop 归类 `tool_error` 返回收尾结构——`TestWriteFailureCircuitBreak` 用异常注入替身 + 真实 DB 故障（drop 表）双重钉死
- **C3 依赖方向**：`evidence_repo.py` 不 import `oncall.harness`（会话/步/假设/收尾结构全走鸭子类型），「归类 tool_error」决策留在 loop 侧；harness → db 单向 import 合法（`oncall.db` 不在 C3 禁列）
- 行 id 回填（D-25）：对账表（step_no → evidence_steps.id、假设池下标 → hypotheses.id）封装 repo 实例状态（C8），`step_row_id`/`hypothesis_row_id` 查询接口暴露；内存契约键集合断言不变（无 id/incident_id 列）
- 覆盖语义（D-31）：`begin` 删同 incident 旧 evidence_steps/hypotheses 行 + investigations 行重置回 running（upsert）+ 旧映射清理，`TestBegin::test_begin_second_investigation_overwrites_old_rows` 钉死
- loop.py 微调：模块 docstring 压缩 1 行（同步补「步进即写落库」描述）、`_execute_step` 末尾隐式 return None（PLR0911 return 计数 7>6 的对策）；view/opening 未动（归 05 票）
- 门禁：**509 passed / 7 skipped**（基线 494+7 只增不减，+15）+ ruff check / ruff format --check 全绿 + 架构守卫全绿 + coverage 98%（≥98% 不回退）；零 LLM 调用、零 HTTP；holdout/ 未触碰；CONTEXT.md 无新术语；不碰 GET /investigations、注册表、models.py、views.py、report.md（归 03/04 票）
