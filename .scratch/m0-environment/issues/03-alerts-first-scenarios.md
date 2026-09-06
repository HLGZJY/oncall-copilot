Status: resolved
Blocked by: 02

# 03 告警规则 + 首批 2 剧本（W1 验收）

## 任务

A. Prometheus 告警规则 5–8 条 + Alertmanager：webhook receiver 最简版（追加写 `deploy/alerts-dump.jsonl`，M1 时替换为 oncall ingest 端点）。
B. 首批 2 个剧本：**01-CPU 飙高**（Pumba）、**02-慢 SQL**（自定义脚本），按 `scenario.yaml` 目录约定落 `chaos/scenarios/01-cpu-spike/`、`chaos/scenarios/02-slow-sql/`，各带 cleanup 脚本。

## 要点

- 从这两个剧本提炼目录约定：`inject 脚本 + scenario.yaml + cleanup 脚本`，供 T4 泛化
- 告警规则与剧本一一对应（`expected_alerts` 即映射的物理载体），防哑剧本

## 验收（W1 口径，实测后回填）

- [x] 注入 CPU 飙高 → PromQL（Prometheus API curl 可查）出现异常曲线 → 告警触发并落 `alerts-dump.jsonl`
  - 实测：Pumba `stress --inject-cgroup` + api-gw 运行时收窄单核（`docker update --cpuset-cpus=0`）+ 128 线程探针打 /health；非 /tasks 路径 P95 从基线 0.0095s 升至 0.091s，`DemoApiGwHighLatency`（阈值 0.05s，for:1m）firing → Alertmanager → `alerts-dump.jsonl` 落 firing/resolved 对（dump 时间戳 06:28 / 06:42 / 07:20 共 3 轮，可重复）
- [x] 注入慢 SQL → 延迟类指标异常 → 对应告警触发
  - 实测：MySQL `LOCK TABLES tasks WRITE + SELECT SLEEP(300)`，12 线程探针并发 POST /tasks；10 连接占满池（`demo_db_pool_used=10`）→ `DemoDbPoolSaturated` firing；池超时产生 500 流量 → `DemoTasksHighLatency`（P95 分支 + 5xx 分支双路）firing；`DemoHighErrorRate` 伴触
- [x] 两个剧本 cleanup 后指标恢复正常，可重复执行
  - 实测：cleanup（KILL 持锁会话 / 停 stress 恢复 cpuset）后 90–120s 内全部告警 resolved，/health 恒 200（8ms），/tasks 500 rate 归零；每剧本实测 ≥3 轮
- [x] 两份 `scenario.yaml` 过校验器（issue 02 产物）
  - `load_scenario_file` 双双通过（cpu-spike=资源类/pumba，slow-sql=业务类/custom-script）

## 实测过程中的关键结论（供 T4 泛化）

1. **空载 I/O 型端点在 CFS 公平调度下饿不出延迟**——CPU 飙高剧本必须叠加业务探针流量才可观测；且 `demo_request_duration_seconds` 是服务端计时，TCP/探针侧排队不计入，阈值必须落在"饥饿后服务内延迟"区间（0.05s）。
2. **正交性靠 /health 独立连接池保证**（demo/common/db.py `health_engine`）：健康检查不得依赖主连接池，否则池饱和会连带 CPU 剧本告警误报。
3. **未处理异常曾绕过指标中间件**（5xx 失明）：中间件已在 except 分支补记 500；endpoint label 须在路由匹配后读取（call_next 返回/抛出时 scope["route"] 才写入），提前读取全部落 "unmatched"。
4. histogram_quantile 的 +Inf 桶 NaN 陷阱：全部样本落入 +Inf 时 quantile 为 NaN，NaN 比较恒假——延迟规则需配 5xx rate 兜底支路（`or`）。

## Blocked by

02
