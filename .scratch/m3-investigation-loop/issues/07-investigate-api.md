Status: resolved
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

- [x] `POST /investigate` 契约测试绿（200 形状键集合 / incident 不存在 404 / mock Planner 脚本驱动端到端返回 InvestigationResult）
- [x] `GET /investigations/{incident_id}` 契约测试绿（有报告 200 / 无报告 404 / escalated 报告可查）
- [x] 调查开局锚点单测绿（卡片以 alert_ids[0] 组装、时间锚 last_fired_at）
- [x] 全量 pytest + ruff 双检绿

## 注记（2026-09-08 实现回填）

落位：`src/oncall/api/investigation.py`（新建：注册表 + build_report + 路由工厂）、
`src/oncall/ingest/app.py`（组装点注入 `InvestigationDeps` + 默认 opening_builder 兜底 + 挂载路由）、
`tests/unit/test_investigation_api.py`（12 用例）、`CONTEXT.md`（新术语「调查报告 / Investigation Report」入表，
`Investigation Result` 词条 _Avoid_ 同步收敛）。

门禁实测：**462 passed / 4 skipped**（基线 450/4 只增不减 +12）、coverage **97.92%**（C9 ≥80%）、
`ruff check .` + `ruff format --check .` 全绿、架构守卫（C3/C6/C8/A1/A2）全绿。

语义口径（均已单测钉死）：
- 报告键集合（12 键，精确守卫）：`{incident_id, termination, conclusion, failure_mode, step_count, total_tokens, total_cost_cny, stop_reason, confidence, opening_card, steps, hypotheses}`
- `confidence` 机械口径：已裁决假设（rejected+confirmed 为分母）中 confirmed 占比，无已裁决假设 → 0.0（M7 LLM-as-judge 复核前的规则值）
- status ≠ investigating（实测 mitigated 行）：仍允许调查、报告如实记录、**不回写 incidents 行**（D-19 枚举冻结）
- 每次调查新建 `InvestigationSession`（踩坑⑪终态单次迁移守卫）；重复调查覆盖注册表旧报告
- 缺省组件 None → POST 503（照 classify_runtime 先例不静默降级）；GET 不依赖组件，按注册表 404/200
- harness 非预期异常 → 500 + `{detail}` 结构化错误体，不泄漏堆栈/异常类名

边界偏差与理由：
1. **报告新增 `opening_card` 键**（票面响应要点未列）：开局卡片若只组装不落报告则成为死代码；落报告后锚点行为可端到端观测（`alert_ids[0]` + `last_fired_at` 断言），也正好是 M8 时间线的第一现场。键集合守卫按 12 键执行。
2. **`routes.py` 未改动**：调查两端点独立成 `create_investigation_router`（issue 内新建文件路径），组装点在 `app.py` 挂载——比塞进既有 routes 工厂更内聚，`create_app` 新增 1 个注入参数（`investigation: InvestigationDeps`，收拢 dataclass）后 6 参触发 PLR0913/PLR0917，已按票面边界用 `# noqa` 带理由豁免（收拢会破坏 M1/M2 测试的 `create_app` 关键字调用面）。
3. **缺省 opening_builder 由组装点兜底**：`InvestigationDeps.opening_builder=None` 时 `create_app` 按 context 配置补装（复用 `build_alert_card`），测试无需自带 ContextConfig。

下一票：08 端到端 3 剧本验证（真实 LLM 调用前需用户确认 key）。
