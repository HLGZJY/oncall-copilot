Status: ready-for-agent
Blocked by: 01

# 02 遥测埋点 + schema 校验器（TDD）

## 任务

A. demo 系统遥测：prometheus_client 输出 QPS / 延迟直方图 / 队列深度 / DB 连接池四类指标；structlog 日志落盘并被 Loki 采集（**Docker loki logging driver**，不引入 promtail）；Grafana 基础看板 1 张。
B. `scenario.yaml` 与黄金集 YAML 的 schema 校验器（**本仓库 M0 唯一 TDD 接缝，先写测试**）：放 `src/oncall/scenarios/`，纯离线可单测（pytest-socket 断网环境须通过）。

## 要点

- 校验器字段契约（D-12 冻结）：`name / fault_type / category / inject / inject_method / cleanup / expected_alerts / expected_root_cause / expected_remediation`；黄金集含 `runs[].alert_timeline[]` + `root_cause` + `investigation_path` + `remediation`，dev/holdout 双集隔离
- 日志格式用 Loki 能解析的结构化 JSON

## 验收（机械判定）

- [ ] `curl /metrics` 含四类指标（QPS/延迟/队列深度/DB 连接）
- [ ] `docker compose logs` 或 Loki API 可查到 demo 结构化日志
- [ ] 校验器：合法样例通过；缺字段/未知 fault_type/黄金集缺 root_cause 等非法样例分别报明确错误——测试先红后绿
- [ ] 全量 pytest 通过，coverage ≥80%（只算 `src/oncall`）

## Blocked by

01
