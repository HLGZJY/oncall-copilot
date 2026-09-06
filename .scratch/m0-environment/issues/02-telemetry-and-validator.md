Status: resolved
Blocked by: 01

# 02 遥测埋点 + schema 校验器（TDD）

## 任务

A. demo 系统遥测：prometheus_client 输出 QPS / 延迟直方图 / 队列深度 / DB 连接池四类指标；structlog 日志落盘并被 Loki 采集（**Docker loki logging driver**，不引入 promtail）；Grafana 基础看板 1 张。
B. `scenario.yaml` 与黄金集 YAML 的 schema 校验器（**本仓库 M0 唯一 TDD 接缝，先写测试**）：放 `src/oncall/scenarios/`，纯离线可单测（pytest-socket 断网环境须通过）。

## 要点

- 校验器字段契约（D-12 冻结）：`name / fault_type / category / inject / inject_method / cleanup / expected_alerts / expected_root_cause / expected_remediation`；黄金集含 `runs[].alert_timeline[]` + `root_cause` + `investigation_path` + `remediation`，dev/holdout 双集隔离
- 日志格式用 Loki 能解析的结构化 JSON

## 验收（机械判定）

- [x] `curl /metrics` 含四类指标（QPS/延迟/队列深度/DB 连接）——`demo_requests_total`、`demo_request_duration_seconds_bucket`、`demo_queue_depth`、`demo_db_pool_used`/`demo_db_pool_size` 全部输出
- [x] Loki API 可查到 demo 结构化日志——`{compose_service="api-gw"}`/`{compose_service="worker"}` 均有纯 JSON 行（task created/task done）
- [x] 校验器：合法样例通过；缺字段/未知 category/未知 inject_method/坏时间戳/多余字段/黄金集 <3 run 分别报明确错误——测试先红后绿
- [x] 全量 pytest 通过，coverage ≥80%（30 测试全绿，coverage 97%）

## Comments

### 2026-09-06 实现（Agent）

- B 部分提交 `a7269ba`：`src/oncall/scenarios/schema.py`（pydantic v2，`extra="forbid"` 抓 typo；category/inject_method 枚举冻结取值；黄金集 runs 强制 ≥3）+ `tests/unit/test_scenarios_schema.py` 20 用例 + `tests/conftest.py`（src 导入路径 + 单测默认断网 A1，pytest-socket 可选降级）+ pyproject 加 pyyaml
- A 部分提交 `4c3893e`：`demo/api_gw/metrics.py` 四类指标 + 中间件埋点 + `demo/common/logsetup.py`（structlog JSON → stdout）+ compose 增 loki/grafana + `deploy/grafana/provisioning/`（Loki 数据源 + compose_service 标签日志看板）
- **坑 1（loki driver）**：插件运行在宿主 daemon 网络，`loki-url` 用 compose 服务名收不到流，必须 `http://127.0.0.1:3100/...`；插件本体 `docker plugin install grafana/loki-docker-driver:latest --alias loki`
- **坑 2（celery 日志劫持）**：worker JSON 行被包 `WARNING/ForkPoolWorker` 前缀——配置键必须带 worker_ 前缀（`worker_hijack_root_logger=False` + `worker_redirect_stdouts=False`），短键名静默无效
- **坑 3（架构守卫）**：`__all__` 被判可变全局——守卫测试加 dunder 豁免
- 环境备注：loki/grafana 端口仅绑 127.0.0.1（3100/3000）；Grafana 匿名 Admin，看板 uid `oncall-demo-logs`
