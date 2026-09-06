# m1-alert-ingestion · Spec

M1 告警接入与归一化。**权威设计**：`docs/design/m1-alert-ingestion-design.md`（status: reviewed，2026-09-06 评审通过，G1–G7 全部定案，标准来源见该文档 §7「评审依据」R1–R5）。

## 目标

Alertmanager receiver 从「写 `alerts-dump.jsonl`」升级为 oncall `POST /ingest`：Pydantic 校验 → 归一化 → 指纹去重合并落 `alert_events`（SQLite）→ 三源上下文组装「事件卡片」JSON 可查。零 LLM，为 M2 分类提供干净分母、为 M3 取证提供复用客户端。

## 关键契约（评审已定，执行时不得擅改）

- **主指纹**：canonical 稳定 label 子集 `{alertname, job, instance}`（存在者）排序 + `0xFF` 分隔拼接 → sha256 hex；易变字段（数值/annotations/generatorURL）不入哈希；**Alertmanager 自带 `fingerprint` 不作主指纹**，存 `annotations_json` 溯源（G1/G5）
- **去重窗口**：默认 10m，配置项 `dedup_window` 可调（G1）
- **去重状态**：`alert_events` 行原地更新（`status=deduped`、`dedup_count`、`last_fired_at`），不引 Redis（G2）
- **增列**：架构 §4 冻结列之上增 `dedup_count / last_fired_at / resolved_at / annotations_json`（D-13）；SQLite `create_all`（dev），Alembic 延至切 MySQL
- **幂等**：`/ingest` 同 payload 重放不重复计数（Alertmanager 非 2xx 会指数退避重试）；`alerts[]` 按批量数组处理，容忍 `truncatedAlerts`（G3 + R1）
- **边界**：零 LLM；不建 incidents 表；不算降噪率（M2）；不做 UI；近期变更源只留占位 adapter（G4/G7）
- **纪律**：Phase A 不切换 receiver（oncall `/ingest` 独立联调）；切换在 06，且须 M0-05 黄金集采集完毕后

## 任务序列

| Issue | 任务 | 对应设计文档 |
|---|---|---|
| 01 | `alert_events` 表与模型（含增列） | T1 |
| 02 | `/ingest` webhook + 归一化（幂等 + 批量） | T2 |
| 03 | 指纹计算 + 去重合并（G1/G2 定案） | T3 |
| 04 | 上下文三源（PromQL / 拓扑 / 变更占位） | T4 |
| 05 | 事件卡片 JSON 契约 + 查询路由 | T5 |
| 06 | receiver 切换 + 端到端演练收尾 | T6 |

## 状态

- [x] 设计评审通过（2026-09-06，G1–G7 定案，依据 R1–R5）
- [ ] Phase A：01–03（接入 + 归一化 + 指纹去重，可并行于 M0-05/06 收尾，不切 receiver）
- [ ] Phase B：04–05（上下文与卡片）
- [ ] Phase C：06（receiver 切换须 M0-05 resolved；端到端演练 + 实测回填设计文档验收节，翻 `implemented`）
