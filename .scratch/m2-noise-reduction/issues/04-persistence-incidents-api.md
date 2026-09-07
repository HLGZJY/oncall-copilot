Status: resolved
Blocked by: 02, 03（均已解除）

# 04 落库与 incidents 建档 + 分类 API（T4 / G4/G5 定案）

## 任务

双通道编排落库，落 `src/oncall/classify/service.py` + `src/oncall/db/` + `src/oncall/api/`：

- **增列（D-19，走 D-13 同款流程）**：`alert_events.classification_json`（verdict/confidence/reason/channel/model/tokens/cost_cny/classified_at）；status `deduped → classified`，枚举不扩
- **incidents 最小集（G5）**：`id / alert_ids(JSON 数组) / severity / status('investigating') / created_at`；真实告警行 1:1 建档（`alert_ids` 单元素数组，为未来归并预留）；severity 取 `labels.severity`（缺省 warning）
- **API**：`POST /classify`（`{alert_id}` 或 `{batch: "all"|"pending"}`，响应含 `{classified, false_positive, risk, incident, llm_calls}`）；`GET /alerts` 增 `verdict` 过滤（SQLite JSON1 `json_extract`，R7）；`GET /incidents` 列表
- **回写**：`alert_events` 增列与 incidents 表建立后，回写架构文档 §4（D-13 先例）

## 要点

- `POST /classify` 是**独立入口，不串联 `/ingest`**（ingest 主链路保持零 LLM、0 漏收不被外部依赖拖垮，D-19/R6）
- 编排顺序：规则通道先行（命中即直判）→ 未决行进 LLM 通道 → 阈值派生 → 落库；一次分类一个事务（classification_json + status 同事务写入）
- verdict 查询走 json_extract，不建独立 verdict 列/索引（11 剧本规模，R7）——若实测查询成为瓶颈再议，不预优化
- `GET /incidents` 是 M3 调查入口的查询面；每条 incident 可经 `alert_ids[0]` 锚到 D-17 事件卡片

## 验收（可机械判定，实测后回填）

- [x] 三态落库单测绿：false_positive/risk/incident 三种结果 → `classification_json` 字段齐全（八键逐项断言）、status 翻 classified、未分类行保持 NULL + deduped（`test_classify_service.py::TestThreeVerdictPersistence`）
- [x] incidents 建档单测绿：incident 判定 → 1:1 建档五字段齐全；risk/false_positive 不建档（`TestIncidentFiling`，含 severity 缺省 warning 边界）
- [x] `POST /classify` 契约测试绿：单条 / batch pending（只处理 deduped 行）/ batch all；重复 classify 幂等（已 classified 行跳过，二跑全零 + 不重复建档）（`test_classify_api.py::TestClassifySingle/TestClassifyBatch`）
- [x] `GET /alerts?verdict=` 与 `GET /incidents` 契约测试绿（`TestAlertVerdictFilter/TestIncidentsEndpoint`；NULL 行不被任何 verdict 命中）
- [x] 架构文档 §4 已回写（classification_json 增列 + incidents 表标注；CONTEXT.md 增「双通道编排」术语）
- [x] 全量 pytest + ruff 绿（pytest 228 passed / 4 skipped / cov 97.11%；ruff check + format --check 全过）

## Comments

- 新增文件：`src/oncall/classify/service.py`（编排）、`src/oncall/db/views.py`（alert_body/incident_body 下沉到 db 层，解开 classify→api 循环导入，card.py 再导出保持公开面）、`tests/unit/test_classify_service.py`、`tests/unit/test_classify_api.py`
- `ClassifyRuntime`（llm_channel + options 收敛为单一注入参数，LLMChannelOptions 先例）；未注入时 /classify 落 503，禁静默 mock
- 加分项落地：架构守卫新增 `test_ingest_chain_has_zero_llm_dependencies`（ingest 主链路四模块禁 import oncall.classify 与 LLM SDK；app.py 组装点豁免）
- DB 契约测试同步扩至 D-19 口径（11 列 + incidents 表），测试名更新为 d13_d19/m2_scope
- 实录：本会话再次出现同文件并行 Edit 报成功未落盘（routes.py/create_router 签名、classify/__init__ 导出、test 文件多处），全部靠 grep 复核补齐——02/03 已知坑①持续有效
- 剩余依赖：issue 05（统计口径）可开工，消费本票落库形态；issue 07（端到端）需用户先确认 LLM API key
