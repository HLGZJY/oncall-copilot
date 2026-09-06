Status: resolved
Blocked by: 02

# 03 指纹计算 + 去重合并（G1/G2 定案）

## 任务

指纹纯函数 + 时间窗去重合并，落 `src/oncall/ingest/`：

- **指纹（G1）**：canonical = `{alertname, job, instance}`（存在者）按名排序 + `0xFF` 分隔拼接 → sha256 hex，写 `alert_events.fingerprint`
- **合并（G2）**：窗口内（默认 10m，配置 `dedup_window`）同指纹重复 firing → 原行更新：`dedup_count++`、`last_fired_at` 刷新、`status=deduped`；resolved 到达 → `resolved_at` 落、状态联动；不新建行、不引 Redis

## 要点

- 指纹必须稳定：同告警重发（数值/annotations 变化）指纹不变；不同实例/规则指纹必不同
- 易变字段（value/annotations/generatorURL/AM fingerprint）**绝不入哈希**——AM 自带 fingerprint 是全 labels FNV-1a，含易变 label，只作溯源
- `0xFF` 分隔与 label 排序是 prometheus/common 先例（设计文档 §7 R2），防 `ab`+`c` 与 `a`+`bc` 拼接碰撞

## 验收（可机械判定，实测后回填）

- [x] 同故障 3 连发 → 1 条记录 `dedup_count=3`，0 漏收
  （2026-09-07 实测：真实 alert 连发 3 次（startsAt 递增 60s）→ 1 行 `dedup_count=3`、`last_fired_at` 刷新到末次、`fired_at` 保持首次；单测同步覆盖「计数合计 = firing 次数」）
- [x] 窗口边界单测绿：窗内合并 / 超窗新行 / `dedup_window` 配置生效
  （2026-09-07 实测：+11min → 新行；`dedup_window=1m` 下距上次 30s 合并、距上次 65s 分行；端点层 `create_app(dedup_window=...)` 端到端生效。注：滑窗按「距上次 firing」计，非距首 firing）
- [x] 指纹稳定性单测绿：annotations 变化指纹不变；instance 变化指纹变；字段顺序无关
  （2026-09-07 实测：易变 label（severity/scenario/pod）不入哈希；alertname/job/instance 任一变则变；dict 顺序无关；0xFF 分隔使 `a`+`bc` 与 `ab`+`c` 不碰撞）
- [x] resolved 到达后 `resolved_at` 落且状态联动正确
  （2026-09-07 实测：resolved 落 `resolved_at` 且不计数；迟到 45min 的 resolved 仍能锚到原行（指纹锚在 startsAt）；窗内再触发 → `resolved_at` 清空重新打开、计数++）
- [x] 同 payload 重放 3 次 → `dedup_count` 不变（幂等回归）
  （2026-09-07 实测：原样重放 2 次 + 单测 3 次 → 计数不变；已合并 3 连发的行再重放末次仍为 3）
- [x] 全量 pytest 绿
  （2026-09-07 实测：104 passed，coverage 98%（ingest 模块 94–100%）；ruff check + format 全绿）

## 实测补充（真实栈，2026-09-07）

- `deploy/alerts-dump.jsonl` 232 条真实 payload 全量回放：received 232 / deduped 167 / 落库 65 行（8 种 labels 组合），最大单行 `dedup_count=6`。
- **0 漏收边界被真实数据验证**：dump 中 firing/resolved 各 116 条，库内计数合计 120 —— 多出的 4 条经脚本证实为「4 条 resolved 通知没有对应 firing」（firing 通知未被采集到），合并逻辑将其计为一次新 firing，属预期兜底而非重复计数；重复 firing（同 alertname+startsAt 多次投递）实测 0 条。

## 设计取舍（需评审确认，已登记 D-14）

**指纹输入含时间窗桶**，`fingerprint` = sha256(canonical label 子集 + 时间窗桶)：
`alert_events.fingerprint` 带唯一约束（D-13 去重锚点），若只由 label 子集算出，则同一告警任何时刻只能有一行，「超窗新行」与 `dedup_window` 无从表达。代价是桶边界附近（如 9:59 与 10:01）分属两行——保守方向，不会漏收。合并查询取「当前桶 + 前一桶」两个候选指纹，实现滑窗语义（跨桶但仍在窗内 → 合并进原行）。

## Blocked by

02
