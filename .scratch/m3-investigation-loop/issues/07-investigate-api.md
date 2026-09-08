Status: ready-for-agent
Blocked by: 06

# 07 调查入口 API（T7 / G2·G7）

## 任务

调查入口与报告查询，落 `src/oncall/api/`（组装点注入 harness 组件，api → harness 方向不在 C3 禁列）：

- **`POST /investigate`**：入参 `{incident_id}`；不存在 → 404；以 `alert_ids[0]` 取 D-17 事件卡片（复用 `build_alert_card`，时间锚 `last_fired_at`）作为调查开局上下文；同步 v1 执行循环，返回 `InvestigationResult`（conclusion/confidence/steps/hypotheses/failure_mode/termination/成本汇总）
- **`GET /investigations/{incident_id}`**：从进程内报告注册表读最近一次调查报告；无记录 → 404；escalated 报告同经此出口（无 UI 阶段的「转人工」落点，G7）
- 报告 JSON 形状照 agent-loop-design 证据链数据形状（steps/hypotheses/conclusion/confidence）

## 要点

- 进程内注册表：dev 单进程语义，M4 落库后替换为读表（设计文档已注明）；容量上限防御（如同 incident 重复调查覆盖旧报告）
- incident 存在但 status ≠ investigating 时的语义要定义并单测（建议：仍允许调查，报告如实记录）
- 契约测试照 M2 `test_classify_api.py` / `test_alert_card_api.py` 先例（键集合精确匹配）
- 零写操作（execute_action 是 L2 stub），四道闸门不适用

## 验收（可机械判定）

- [ ] `POST /investigate` 契约测试绿（200 形状键集合 / incident 不存在 404 / mock Planner 脚本驱动端到端返回 InvestigationResult）
- [ ] `GET /investigations/{incident_id}` 契约测试绿（有报告 200 / 无报告 404 / escalated 报告可查）
- [ ] 调查开局锚点单测绿（卡片以 alert_ids[0] 组装、时间锚 last_fired_at）
- [ ] 全量 pytest + ruff 双检绿
