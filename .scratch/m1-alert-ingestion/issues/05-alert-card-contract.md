Status: ready-for-agent
Blocked by: 03, 04

# 05 事件卡片 JSON 契约 + 查询路由（G3 定案）

## 任务

事件卡片组装与查询路由（无 UI 阶段的验收载体，落 api 层）：

- `GET /alerts/{id}/context` → 事件卡片 JSON：**告警本体（归一化字段）+ 近期指标 + 服务拓扑 + 近期变更（可为空）**
- 可另加 `GET /alerts` 列表（分页/按指纹查），不做 UI

## 要点

- 术语纪律：这是**事件卡片 / Alert Card**（CONTEXT.md 已登记），不是「事件 / Incident」——M2 判定真实后才建档，措辞与字段命名不得混用
- 契约字段定型后是 M8 UI 与 M3 取证的输入，命名一次到位（评审过的 JSON 键名不再改）
- 上下文缺失源返回显式 `unavailable` 标记，不留 null 歧义

## 验收（可机械判定，实测后回填）

- [ ] 对 03 产出的合并告警，GET 返回字段齐全的卡片 JSON（本体 + 三源，缺失源有标记）
- [ ] 卡片键名与设计文档/本 spec 契约一致（测试断言固定键名）
- [ ] 不存在的 id → 404；全量 pytest 绿

## Blocked by

03, 04
