Status: ready-for-agent
Blocked by: 02, 03

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

- [ ] 三态落库单测绿：false_positive/risk/incident 三种结果 → `classification_json` 字段齐全、status 翻 classified、未分类行保持 NULL + deduped
- [ ] incidents 建档单测绿：incident 判定 → 1:1 建档五字段齐全；risk/false_positive 不建档
- [ ] `POST /classify` 契约测试绿：单条 / batch pending（只处理 deduped 行）/ batch all；重复 classify 幂等（已 classified 行跳过）
- [ ] `GET /alerts?verdict=` 与 `GET /incidents` 契约测试绿
- [ ] 架构文档 §4 已回写（classification_json 增列 + incidents 表标注）
- [ ] 全量 pytest + ruff 绿

## Comments

-
