Status: ready-for-agent
Blocked by: 02

# 03 报告读库与 JSON 形状定案（T3 / G6；D-35/D-31）

## 任务

- `GET /investigations/{incident_id}`（`src/oncall/api/investigation.py`）：进程内 `ReportRegistry` 换**读库**（D-25 承诺「M4 落库后换读表」）；无调查记录 → 404；escalated 报告同经此出口（D-28）
- **JSON 形状以 M3 `build_report` 现契约为准**（D-35：键集合契约测试已精确守卫——`incident_id/termination/conclusion/failure_mode/step_count/total_tokens/total_cost_cny/stop_reason/confidence/opening_card/steps/hypotheses`）；读库序列化落 `db/views.py`（现 54 行，扩序列化器），api 只组装
- **修订 `docs/architecture/agent-loop-design.md` 示例**：`step→step_no`、`input→input_json`、`cost→cost_cny`、`supporting→supporting_steps`、`against→against_steps` 对齐冻结契约（消除双权威；回写 diff 随本票提交，交用户复核）
- `confidence` 口径沿用现实现（已裁决假设 confirmed 占比，active 不计）

## 要点

- 读库报告须与写库前内存导出的报告逐字段一致（roundtrip 对账）——这是换读库的回归锚
- `opening_card` 来源：写库时随 `investigations` 行留存（JSON 列）或从 `alert_events` 重建 D-17 卡片——倾向前者（一次性留存、读路径零重建），实现票内定，若走后者须注明重建口径与 D-17 键集合一致
- 报告 JSON 形状属冻结契约：**不改键名、不增删键**，本票只换数据来源

## 验收（可机械判定）

- [ ] pytest 绿：写库 → 读库 → 报告与内存导出逐字段 roundtrip 一致
- [ ] pytest 绿：报告键集合精确守卫测试更新后全绿（键名与 M3 完全一致）
- [ ] pytest 绿：无记录 404、escalated 报告读库可查、重复调查后读到的是最新一次
- [ ] agent-loop-design 示例键名与冻结契约一致（文档 diff 落盘）
- [ ] 全量门禁不回退 + ruff 双检
