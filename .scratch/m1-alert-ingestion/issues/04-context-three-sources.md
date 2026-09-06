Status: resolved
Blocked by: 02

# 04 上下文三源（PromQL / 拓扑 / 变更占位）

## 任务

上下文拉取模块落 `src/oncall/context/`，按 alert 的 labels 组装三源：

1. **近期指标（G4 必做）**：Prometheus HTTP API / PromQL 查近期序列（默认近 30m，可配），以 alert 相关 label（instance/job）过滤
2. **服务拓扑（G4 必做）**：`/api/v1/targets` + labels 还原服务间关系
3. **近期变更（G4 占位）**：adapter 返回空列表，接口形状与另两源对齐（列表 + 来源标记），不报错、不阻塞

## 要点

- PromQL 客户端是 M3 取证要复用的接缝，收敛成独立模块，M1 不做缓存/重试的花活（超时给个默认值即可）
- Prometheus 不可达时降级返回「源不可用」标记而非抛错——上下文缺失不等于告警缺失（0 漏收纪律）
- 查询窗口等参数走配置，不硬编码

## 验收（可机械判定，实测后回填）

- [x] 对一条落库告警可取回近 N 分钟指标序列 JSON（Prometheus 在线时）
  实测（2026-09-07）：告警 #2（DemoApiGwHighLatency，labels 无 instance/job → selector
  回落 alertname 路径亦验证）→ `metrics` status=ok，2 条 series，窗口 30m / step 30s。
- [x] 拓扑 JSON 含 targets 及其 labels
  实测：`topology` status=ok，target_count=2（demo-api-gw / prometheus），items 含
  labels/health/last_error，meta.services=[demo-api-gw, prometheus]。
- [x] 变更源返回空列表不报错
  实测：`changes` status=ok，items=[]，meta 注明 G4 占位。
- [x] Prometheus 停止时三源返回降级标记、接口不 5xx
  实测：`docker compose stop prometheus` 后 metrics/topology 均 status=unavailable +
  meta.reason（WinError 10061 连接拒绝），changes 仍 ok，`collect_context` 不抛错
  （exit=0）。恢复容器后全部回 ok。
- [x] 单测绿（HTTP 层 mock）；全量 pytest 绿
  实测：19 个新单测（FakeFetcher 注入，A1 断网合规，不 import httpx）；
  全量 pytest 绿，coverage 96.25%（≥80%）；ruff check / ruff format --check 绿；
  真连联调 tests/integration/test_prometheus_live.py 4/4 过（ONCALL_RUN_INTEGRATION=1）。

## 实现备注（2026-09-07）

- HTTP 收口：C4 守卫要求 httpx 全仓只许在 `oncall/infra/http.py`——本次一并落地该
  接缝（`Fetcher` 协议 + `HttpxFetcher`，trust_env=False 防本机代理拦 127.0.0.1）。
- **顺带修复守卫潜伏 bug**：`test_no_bare_http_clients` 白名单写的是
  `oncall/infra/http.py`，但 `_rel_module` 返回含 `src/` 前缀的路径——白名单从未
  生效，第一个真实收口文件落地即触发。已改为 `src/oncall/infra/http.py`（诊断假设
  已用守卫输出证实：报错对象恰为白名单目标文件）。
- 落库数据来源：回放 `deploy/alerts-dump.jsonl` 末尾 8 条有效 webhook（232 有效行，
  历史截断碎片 try/except 跳过），received=8 / deduped=6（issue 03 合并语义未破坏）。
- 配置口径已登记 `decisions.md` D-16；悬空引用修复：decisions.md 补回缺失的 D-13 行
  （architecture.md 与设计文档均引用它）。

## Blocked by

02
