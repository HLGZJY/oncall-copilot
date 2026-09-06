Status: ready-for-agent
Blocked by: 02

# 03 告警规则 + 首批 2 剧本（W1 验收）

## 任务

A. Prometheus 告警规则 5–8 条 + Alertmanager：webhook receiver 最简版（追加写 `deploy/alerts-dump.jsonl`，M1 时替换为 oncall ingest 端点）。
B. 首批 2 个剧本：**01-CPU 飙高**（Pumba）、**02-慢 SQL**（自定义脚本），按 `scenario.yaml` 目录约定落 `chaos/scenarios/01-cpu-spike/`、`chaos/scenarios/02-slow-sql/`，各带 cleanup 脚本。

## 要点

- 从这两个剧本提炼目录约定：`inject 脚本 + scenario.yaml + cleanup 脚本`，供 T4 泛化
- 告警规则与剧本一一对应（`expected_alerts` 即映射的物理载体），防哑剧本

## 验收（W1 口径，实测后回填）

- [ ] 注入 CPU 飙高 → PromQL（Prometheus API curl 可查）出现异常曲线 → 告警触发并落 `alerts-dump.jsonl`
- [ ] 注入慢 SQL → 延迟类指标异常 → 对应告警触发
- [ ] 两个剧本 cleanup 后指标恢复正常，可重复执行
- [ ] 两份 `scenario.yaml` 过校验器（issue 02 产物）

## Blocked by

02
