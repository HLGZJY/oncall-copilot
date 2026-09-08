Status: ready-for-agent
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

- [ ] pytest 绿：mock 3 步调查后 `evidence_steps` 逐行与 session.steps 逐字段一致（步进即写非批量）
- [ ] pytest 绿：escalated 与 aborted 路径已取证部分完整落库、`investigations` 终态行正确
- [ ] pytest 绿：行 id 回填后指针接口断言不变（D-25 契约键集合不破）
- [ ] pytest 绿：落库异常注入 → 熔断归类 `tool_error`、无静默
- [ ] pytest 绿：同 incident 二次调查覆盖旧行，库内只剩最近一次
- [ ] 全量门禁不回退 + ruff 双检
