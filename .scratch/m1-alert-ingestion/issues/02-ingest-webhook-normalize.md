Status: ready-for-agent
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

- [ ] 真实 Alertmanager（或按 M0-03 实测 payload 构造）POST `/ingest` → 2xx 且落库，字段齐全
- [ ] 同一 payload 重放 3 次 → 仍 1 条记录（03 合入前可先按原始标识判重），响应计数正确
- [ ] 单次 payload 含 ≥2 条 `alerts[]` → 逐条落库
- [ ] 非法 payload → 4xx，服务不崩
- [ ] 归一化纯函数单测绿；全量 pytest 绿

## Blocked by

01
