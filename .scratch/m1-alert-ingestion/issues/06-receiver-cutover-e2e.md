Status: ready-for-agent
Blocked by: 01, 02, 03, 04, 05 + M0-05（黄金集采集完毕后方可切 receiver）

# 06 receiver 切换 + 端到端演练收尾（G6 定案）

## 任务

- `deploy/` Alertmanager receiver 目标 URL 从 dump 端点切到 oncall `/ingest`；**环境开关控制双写**（dump + DB 同写过渡，切稳后关 dump），可随时回退
- 端到端演练 1 个剧本（M0-04 已交付的 11 个剧本中选 1）：注入 → 多告警 → 合并 1 条 → 卡片带上下文，实录回填设计文档验收节
- M1 收尾：设计文档验收节实测回填、`status → implemented`、本 tracker 逐票 resolved

## 要点

- 切换时点硬约束：**M0-05 黄金集采集完毕后**才切（黄金集靠 dump 采集，见设计文档 G6）；双写窗口内 dump 行为不变
- 若切换期出现非 2xx，Alertmanager 指数退避重试天然补偿（评审依据 R1/R3），但 `/ingest` 幂等必须先经 02/03 验收
- 回退演练：关 oncall receiver → 开 dump，确认采集链路恢复

## 验收（可机械判定，实测后回填）

- [ ] receiver 指向 oncall：真实注入 1 剧本 → 3 连发合并 1 条（`dedup_count=3`）→ GET 卡片带上下文，实录（告警名/时间线/dedup_count）回填设计文档
- [ ] 双写开关：开 = dump 与 DB 同写；关 = 仅 DB；dump 关闭后 ingest 不受影响
- [ ] 回退演练成功：切回 dump 后黄金集采集链路可用
- [ ] 全量 pytest 绿；coverage ≥80% 维持
- [ ] 设计文档验收节回填、status 翻 `implemented`；spec 状态节更新

## Blocked by

01, 02, 03, 04, 05 + M0-05
