Status: resolved
Blocked by: 03

# 04 确认门 API（T4 / G2 / D-40）

## 任务

- 新建 `src/oncall/api/remediation.py`（新文件，行数预算 ≈140）：
  - `POST /remediations/{proposal_id}/confirm` —— **第二道闸门（API 层拦截，非提示词）**：body `{decision: "approve"|"reject", reason?: str}`
    - **approve 同步执行**（D-40 confirm 即执行）：受控执行 + 恢复验证（issue 05/06 注入）同步完成后返回终态（demo 处置秒级 + 验证窗口 ≤60s → ≤ ~2.5min < 5min 预算）
    - reject：提案落 rejected + reason 留痕
    - 不存在 / 已终态 → 4xx
  - `GET /remediations/{proposal_id}`：处置查询——干跑预览/状态/确认理由/执行输出摘要/验证结果/回滚状态（无 UI 阶段处置留痕载体）
  - `GET /remediations?incident_id=`：按事件查处置列表（一事件可能多次处置尝试）
- 确认 reason 留痕；approve/reject 均带审计（时间戳）

## 要点

- **提案不自动过期**（D-40）：pending 期 incident 保持 investigating，人工可随时 confirm/reject；不做超时熔断提案（自动作废人类未回复的处置请求违反 D-28「转人工不是丢弃」）
- 处置管线无 LLM——confirm 端点同步执行是确定性系统行为
- api → remediation 单向依赖合法（照 api→classify→db 同构）
- M8 不做 UI——本票确认门 = REST API 最小形态（curl 可确认）
- 注入面：受控执行器 + 恢复验证器经组装注入（照 issue 02 Protocol 接缝先例）；未注入（降级形态 D-48）时 approve 落「建议已确认，执行能力未配置」503/说明 + proposal 留 approved 即终

## 验收（可机械判定）

- [x] pytest 绿：confirm approve → 触发注入替身执行器 + 验证器（调用断言），同步返回终态
- [x] pytest 绿：confirm reject → proposal 落 rejected + reason 落库
- [x] pytest 绿：GET /remediations/{id} 返回干跑预览/状态/理由/执行摘要/验证结果（mock 数据）
- [x] pytest 绿：GET /remediations?incident_id= 返回该事件全部处置（含多次尝试）
- [x] pytest 绿：4xx 语义——不存在 404、已终态再 confirm 409/4xx、decision 非法 422
- [x] pytest 绿：approve 后 dry_run_json 原样作为执行清单（批准对象锁定断言）
- [x] pytest 绿（降级路径 D-48）：未注入执行器时 approve → 503 + 说明 + proposal 留 approved 即终
- [x] 全量门禁不回退（基线 ~~541/10~~ **修正为 609/10**，见派工 prompt 踩坑③；实测 621 passed / 10 skipped，只增）+ ruff 双检全绿 + 架构守卫 7 passed

## 落位注记（实现票回填）

- **端点最终契约**：
  - `POST /remediations/{proposal_id}/confirm`：body `{decision: "approve"|"reject", reason?: str}`（Pydantic `ConfirmRequest`，`Literal` 约束 + `extra=forbid` → decision 非法/多余字段 422）；approve/reject 均 200 返回**终态（或 rejected）proposal 行全量 JSON**；不存在 404；已终态/非法迁移 409（api 层捕获 `ProposalStateError` 映射，不自行判断状态）；D-48 降级 approve → 503 + detail「建议已确认，执行能力未配置…」+ proposal 留 approved 即终
  - `GET /remediations/{proposal_id}`：proposal 行全量 JSON；不存在 404
  - `GET /remediations?incident_id=`：`{"incident_id", "count", "items": [行 JSON…按 id 序]}`
- **终态返回形状**：D-46 八表字段全量 16 键（`id/incident_id/investigation_id/runbook_slug/action_id/status/dry_run_json/params_json/decision/confirm_reason/confirmed_at/executed_at/verify_result_json/rollback_status/created_at/finished_at`，时间戳 ISO 8601）；approve 恢复 → `status=recovered`，未恢复 → `status=failed`（回滚/转人工分叉归 06）；**执行输出摘要落 `params_json["execution"]`**（D-46 行内留痕载体，GET「执行摘要」来源）
- **注入面落位**：`RemediationExecutor` Protocol → `src/oncall/remediation/executor.py`（真实实现归 05 同名落位）；`RecoveryVerifier` Protocol → `src/oncall/remediation/verifier.py`（真实实现归 06）；均结构协议照 issue 02 先例，实例经 `RemediationDeps`（frozen dataclass）→ `create_app(remediation=...)` → `create_remediation_router(engine, deps)` 注入；缺省 `RemediationDeps()` 即 D-48 降级形态，reject/GET 不受降级影响
- **降级路径实测**：未注入时 approve → 503 + proposal 落 approved（decision/confirm_reason/confirmed_at 已留痕）；approved 后再 confirm → 409（approved 无出边）
- **同步链落点**：api 层编排 `approve → start_execution → executor.execute(dry_run_json)（输出摘要落 params_json）→ verifier.verify(dry_run_json) → mark_recovered/mark_failed`，一行不重写状态逻辑（remediation service 单一权威）；执行器/验证器收到的唯一输入 = `dry_run_json` 原文（deep-equal 断言，批准对象锁定 D-39）
- **门禁实测**：全量 `621 passed / 10 skipped`（基线 609/10 + 本票 12）；ruff check / format --check 全绿；架构守卫 7 passed（C3 api→remediation 合法、remediation 零 import harness、C6 api 文件 139 行 < 300）
