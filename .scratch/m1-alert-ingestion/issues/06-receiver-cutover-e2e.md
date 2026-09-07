Status: resolved
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

## Comments

### 2026-09-07 收尾完成（receiver 切换 + 端到端演练实录）

**门槛确认**（用户拍板）：M0-05 并行放行（采集已完毕、数据已落盘，双写窗口 dump 行为不变，抽核/双签由人并行推进）；部署形态选 **oncall 收编进 compose**。

**交付**：
- **三档 receiver env 开关**（纯 yaml，验收证据=演练实录）：`alertmanager.yml`（dump，默认/回退档）/ `alertmanager-dual.yml`（双写过渡）/ `alertmanager-oncall.yml`（终态关 dump）。compose 挂载走 `${ONCALL_AM_CONFIG:-alertmanager.yml}` 插值，切换 = `ONCALL_AM_CONFIG=alertmanager-xxx.yml docker-compose up -d alertmanager`，默认档是 dump（不改环境不变，安全默认）。
- **oncall 收编 compose**：`deploy/oncall/Dockerfile`（python:3.11-slim 对齐 C1，清华源装依赖）+ compose service（`./src` 直挂免重建、仓库根挂 `/app/repo` 使 oncall.db 宿主可见、`ONCALL_PROMETHEUS_URL=http://prometheus:9090` 容器网内三源可达、healthcheck 打 /alerts）。根 `.dockerignore` 防 .git/datasets 拖慢构建。

**演练实录**（02-slow-sql，DURATION=240s，告警归零后才进下一步；fired_at/resolved_at 全取实测）：
1. **dual 档 3 循环**：注入 10:00:12 / 10:05:37 / 10:09:35 → DemoTasksHighLatency firing 10:02:27 / 10:07:21 / 10:11:13 → id 3/4/5 各 `dedup_count=3`；DemoHighErrorRate 迟触发计入（id 6，0 漏收）；resolved_at 全部落库；dump 232→235 与 DB 同步（双写开）。`GET /alerts/5/context`：本体 13 键 + 三源（metrics 3 series / topology 2 targets / changes 占位空）。
2. **dump 回退档**：注入 10:14:48 → firing 10:16:32 → dump 256→259、DB 纹丝不动；collect_run.sh 依赖面完好（dump + Prom API + 注入脚本）。
3. **oncall 档（终态）**：切档 02:19:15Z，注入 10:19:17 → firing 10:21:02 → DB 新增 id 7–10（firing+resolved 联动），dump 侧仅收到切档前迟到通知（endsAt ≤02:19:06Z，无串档）；ingest 不受 dump 关闭影响。

**诊断记录**（诊断类：最终被证实的假设）：oncall 档 firing 未并入 id 3–6 而是开新行——假设"漏合并/串档"，经 service.py + fingerprint.py 核对**证实为 D-14 预期行为**：指纹含时间窗桶（创建桶固定，候选=当前桶+前桶），02:20+ firing 跨出 id 3–6 创建桶两桶覆盖 → 新行，"保守方向，不会漏收"；dump 259→264 的 5 行全部为切档前回退档迟到通知（DemoHighErrorRate 02:17:56Z 迟触发 + 4 条 resolved）。

**门禁**：pytest 147（143 passed + 4 skipped integration），coverage 95.89%，ruff check + format 全绿。

**遗留（不阻塞本票）**：M0-05 人工抽核 ≥1 剧本 + 双签仍待用户完成（与本票并行）。
