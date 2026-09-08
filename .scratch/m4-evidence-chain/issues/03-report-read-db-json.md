Status: resolved
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

- [x] pytest 绿：写库 → 读库 → 报告与内存导出逐字段 roundtrip 一致（`test_roundtrip_matches_in_memory_export_field_by_field`；唯一隐性差异 = pydantic UTC 渲染 `Z` 后缀，views 侧 `_step_ts_iso` 对齐同口径）
- [x] pytest 绿：报告键集合精确守卫测试更新后全绿（键名与 M3 完全一致，REPORT_KEYS 未动）
- [x] pytest 绿：无记录 404、escalated 报告读库可查、重复调查后读到的是最新一次（另补：running 行 404）
- [x] agent-loop-design 示例键名与冻结契约一致（文档 diff 落盘：`step→step_no` / `input→input_json` / `cost→cost_cny` / `supporting→supporting_steps` / `against→against_steps`，仅换点名 5 键）
- [x] 全量门禁不回退 + ruff 双检（518 passed / 7 skipped，coverage 98%；ruff check + format 全绿）

## 实现注记（2026-09-08）

- **opening_card 来源定案 B（偏离票面倾向）**：`investigations` 加 `opening_card_json` JSON 列（超出设计 §数据模型变更清单，已在提交 body 论证）。理由：A（读时重建）的卡内 `generated_at` 是构建时刻时间戳，重建必然漂移 → 「GET == POST 全量对账」与「roundtrip 逐字段一致」两条验收在 A 下不成立；B 一次性留存、读路径零重建。
- **build_report 保留**：作 POST 写路径内存导出口径 + roundtrip 对账基准，键集合契约测试继续守卫。
- 唯一键名映射点：ORM 冻结列 `cost` → 内存契约 `cost_cny`（views 序列化器内，D-25 不倒改内存侧）。
- **风险移交**：现库 `oncall.db` 的 investigations 表为 T1 旧形状（无 `opening_card_json` 列），`create_all` 不做 ALTER——T8 真实实测前需对该库手工 `ALTER TABLE investigations ADD COLUMN opening_card_json JSON` 或等价迁移。
- confidence 读库重算口径与内存一致（rejected+confirmed 为分母、active 不计、无已裁决 → 0.0），有专项单测（含乱序步号排序、入池序、透传 opening_card）。
