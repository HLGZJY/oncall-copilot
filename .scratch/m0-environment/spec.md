# m0-environment · Spec

M0 环境与混沌注入。**权威设计**：`docs/design/m0-environment-design.md`（status: reviewed，2026-09-06 评审通过）。

## 目标

docker-compose 一键拉起被观测 demo 系统（api-gw → worker → MySQL/Redis）+ Prometheus/Loki/Grafana + 5–8 条告警规则 + 8+ 故障剧本注入脚本 + 黄金评测集，为 M1–M7 提供真实数据底座。

## 关键契约（评审已定，执行时不得擅改）

- 目录布局：`demo/`、`deploy/`、`chaos/scenarios/`、`datasets/golden/`，均**不进 `src/oncall`**（D-12）
- `scenario.yaml` 九字段：`name / fault_type / category / inject / inject_method / cleanup / expected_alerts / expected_root_cause / expected_remediation`
- Loki 走 Docker loki logging driver；ruff 覆盖 demo/chaos，mypy 豁免；coverage 只算 `src/oncall`
- 黄金集 dev/holdout 双集隔离（架构文档 §6）

## 任务序列

| Issue | 任务 | 对应设计文档 |
|---|---|---|
| 01 | demo 业务系统骨架 | T1 |
| 02 | 遥测埋点 + schema 校验器（TDD） | T2 |
| 03 | 告警规则 + 首批 2 剧本（W1 验收） | T3 |
| 04 | 注入框架泛化 + 剩余剧本补齐 | T4 |
| 05 | 黄金集生成 | T5 |
| 06 | 一键复现与收尾 | T6 |

## 状态

- [x] 设计评审通过（2026-09-06）
- [ ] Phase A：01–03（W1 验收口径：环境跑通 + 2 剧本）
- [ ] Phase B：04–06（剧本补齐 + 黄金集 + 收尾）
