Status: ready-for-agent
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

- [ ] pytest 绿：confirm approve → 触发注入替身执行器 + 验证器（调用断言），同步返回终态
- [ ] pytest 绿：confirm reject → proposal 落 rejected + reason 落库
- [ ] pytest 绿：GET /remediations/{id} 返回干跑预览/状态/理由/执行摘要/验证结果（mock 数据）
- [ ] pytest 绿：GET /remediations?incident_id= 返回该事件全部处置（含多次尝试）
- [ ] pytest 绿：4xx 语义——不存在 404、已终态再 confirm 409/4xx、decision 非法 422
- [ ] pytest 绿：approve 后 dry_run_json 原样作为执行清单（批准对象锁定断言）
- [ ] pytest 绿（降级路径 D-48）：未注入执行器时 approve → 503 + 说明 + proposal 留 approved 即终
- [ ] 全量门禁不回退（基线 541/10）+ ruff 双检

## 落位注记（实现后回填）

- （待实现票回填：端点最终契约、终态返回形状、降级路径实测）
