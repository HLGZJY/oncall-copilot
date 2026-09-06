Status: ready-for-agent
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

- [ ] 同故障 3 连发 → 1 条记录 `dedup_count=3`，0 漏收
- [ ] 窗口边界单测绿：窗内合并 / 超窗新行 / `dedup_window` 配置生效
- [ ] 指纹稳定性单测绿：annotations 变化指纹不变；instance 变化指纹变；字段顺序无关
- [ ] resolved 到达后 `resolved_at` 落且状态联动正确
- [ ] 同 payload 重放 3 次 → `dedup_count` 不变（幂等回归）
- [ ] 全量 pytest 绿

## Blocked by

02
