Status: resolved
Blocked by: 01

# 02 /ingest webhook + 归一化

## 任务

FastAPI `POST /ingest` 端点落 `src/oncall/ingest/`：Pydantic v2 校验 Alertmanager webhook v4 JSON → 归一化为内部 alert 结构 → 落 `alert_events`。响应 `2xx + {received, deduped}`。

**评审硬要求**（设计文档 G3/R1）：

- **幂等**：同一 payload 原样重放不产生新记录、不重复计数（Alertmanager 对非 2xx 指数退避重试，重复投递是常态）
- **批量**：`alerts[]` 是数组，逐条归一化，容忍 `truncatedAlerts` 截断场景；不假设单条
- **溯源**：AM 自带 `fingerprint`、原始 payload 存 `annotations_json`，主指纹由 issue 03 计算（本票先落原始行，03 完成后接上指纹去重）

## 要点

- 归一化映射集（canonical 字段映射）写纯函数，是 TDD 接缝
- 双写：`deploy/` 的 dump receiver **本票不动**（切换在 06）；oncall 端点独立联调
- 校验失败返回 4xx（让 AM 侧可见异常），不吞错

## 验收（可机械判定，实测后回填）

- [x] 真实 Alertmanager（或按 M0-03 实测 payload 构造）POST `/ingest` → 2xx 且落库，字段齐全
  （2026-09-06 实测：M0 compose 在跑，用 `deploy/alerts-dump.jsonl` 232 条真实历史 payload 全量回放，全部 2xx；落库字段 source/labels_json/fired_at/status/annotations_json 逐列核验齐全）
- [x] 同一 payload 重放 3 次 → 仍 1 条记录（03 合入前可先按原始标识判重），响应计数正确
  （2026-09-06 实测：真实 firing payload 重放 3 次 → `{received:1, deduped:0/1/1}`，仍 1 行，dedup_count=1 不变；判重锚点 = AM 自带 fingerprint + 原始 alert 规范化 JSON 的 sha256，issue 03 接主指纹后替换）
- [x] 单次 payload 含 ≥2 条 `alerts[]` → 逐条落库
  （2026-09-06 实测：单测批量用例 + payload 内自重复判重用例均绿；dump 无真实多 alert 行，批量语义由单测覆盖）
- [x] 非法 payload → 4xx，服务不崩
  （2026-09-06 实测：缺 alerts/缺 startsAt/非数组/畸形 JSON → 422；后续合法请求照常 2xx 落库）
- [x] 归一化纯函数单测绿；全量 pytest 绿
  （2026-09-06 实测：pytest 80 passed，coverage 97.83%；ruff check + format 全绿）

## Blocked by

01
