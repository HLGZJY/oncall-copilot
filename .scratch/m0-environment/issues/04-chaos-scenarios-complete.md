Status: resolved
Blocked by: 03

# 04 注入框架泛化 + 剩余剧本补齐（至 8+）

## 任务

从 01/02 剧本提炼通用模式后，补齐剩余剧本至 ≥8 类，覆盖 m0-execution 六大类：

| # | 剧本 | 注入手段 | 类别 |
|---|---|---|---|
| 03 | OOM | cgroup memory.max（Pumba 偏离，见下） | 资源类 |
| 04 | 下游超时 | Pumba | 网络类 |
| 05 | 网络丢包 | Pumba | 网络类 |
| 06 | 死锁 | 自定义脚本 | 业务类 |
| 07 | 队列堆积 | 容器化探针（Locust/k6 等价替身） | 负载类 |
| 08 | 连接打满 | 容器化探针 | 负载类 |
| 09 | 进程被杀 | Pumba | 故障类 |
| 10 | 缓存雪崩 | 自定义脚本 | 故障类 |
| 11 | **版本协议不兼容（语义层，必做）** | 自定义 | 业务语义层 |

### 注入手段偏离说明（实测决策）

- **03 OOM 未用 Pumba**：Pumba stress-vm 注入 cgroup 后，内核 OOM killer 杀的是
  cgroup 内"坏度"最高的进程（通常是 stress-ng 自己），故障不可观测。改用
  `docker update --memory` 把上限压到 RSS 的 60%，确定性 OOM（实测 ~25s 内
  OOMKilled 退出）。
- **07/08 未用 Locust/k6**：沿 01/02 先例用单容器 python 探针（等价负载源，
  无额外镜像依赖），inject_method 记 `load-generator`。
- **Pumba 端口过滤在 Docker Desktop/WSL2 上不生效**：`--ingress-port 3306` 按
  端口只延迟 mysql 流量的两种目标（api-gw 出向 / mysql 入向）实测均被忽略。
  04/05 改为全流量损伤（04 延迟 700ms、05 丢包 25%），延迟取值以"顶过告警阈值
  且不超抓取超时 4s"为界——3s 全流量延迟会把 up 打成 0，DemoApiGwDown 误触发、
  真实告警失明（观测面与故障共沉浮的反面教材，已写进剧本头注释）。

## 要点

- 语义层剧本的特性：指标日志都正常但业务出错——告警规则需覆盖日志关键字或业务指标维度
  → 11 号的告警落在业务指标 `demo_tasks_stale_pending`（pending 超 60s 计数），
  firing 期间实测无 5xx、队列正常排空，语义层特征完整
- 每个剧本在 `scenario.yaml.expected_alerts` 写清预期告警，并按实测修正

## 验收（机械判定）

- [x] `chaos/scenarios/` 下 11 个目录（不含黄金集），六类全覆盖，语义层有（11 号）
- [x] 每剧本注入 → 预期告警触发 → cleanup 恢复，可重复执行；`scenario.yaml` 全过校验器
- [x] 无哑剧本：每个剧本至少对应 1 条已配置的告警规则
      （守卫测试 `tests/unit/test_scenario_catalog.py` 把 expected_alerts ⊆ rules.yml
      已配置规则名固化为断言，同时校验 ≥8 剧本 / 六类全覆盖 / 语义层必存在）

## 实测记录（2026-09-06，逐剧本 注入→告警→清理→恢复 闭环）

| 剧本 | 触发告警（firing 实证） | 关键观测 |
|---|---|---|
| 03 oom-kill | DemoApiGwDown | ~25s 内 OOMKilled 退出；恢复后 up=1 |
| 04 downstream-timeout | DemoTasksHighLatency / DemoDbPoolSaturated / DemoApiGwHighLatency / DemoQueueDepthHigh / DemoTasksStuckPending | 700ms 延迟；up 全程 1 |
| 05 packet-loss | DemoTasksHighLatency / DemoDbPoolSaturated / DemoApiGwHighLatency / DemoQueueDepthHigh | 25% 丢包；up 全程 1 |
| 06 db-deadlock | DemoTasksHighLatency / DemoDbPoolSaturated / DemoHighErrorRate | 12 个 INSERT 等待在 supremum 锁；data_locks 实证锁环 |
| 07 queue-backlog | DemoQueueDepthHigh / DemoTasksStuckPending | 净堆积 ~7/s；清理后 1374 条 ~9/s 排空 |
| 08 pool-exhaustion | DemoDbPoolSaturated / DemoCacheMissSpike / DemoApiGwHighLatency | 池 1m 平均稳定 >70%；恢复后 0 告警 |
| 09 process-killed | DemoQueueDepthHigh / DemoTasksStuckPending | ExitCode=137；净增 ~24/s；扩容 worker=4 加速排空 |
| 10 cache-avalanche | DemoCacheMissSpike / DemoApiGwHighLatency | miss 率 ~160/s（50ms 间隔 FLUSHDB） |
| 11 protocol-mismatch | DemoTasksStuckPending | worker 日志 incompatible；补偿 145 条滞留任务 |

每剧本 cleanup 后告警全部 resolved（最终 `ALERTS` 查询为空），alerts-dump.jsonl
同步落盘 firing/resolved 事件。

### 实测揪出的三个环境/代码缺陷（均已修复）

1. **demo_queue_depth 恒为 0（潜伏 bug）**：gauge 读 redis db0，而 celery broker
   在 db1——`DemoQueueDepthHigh` 此前是死规则。修复：新增 broker_client 读 broker db。
2. **/metrics 与被观测故障共沉浮（本次引入又当场修复）**：新增的 stale pending
   统计让 /metrics 第一次依赖主连接池，06 死锁打满池时抓取失联 → DemoApiGwDown
   误触发。修复：专用短超时引擎 metrics_engine（read_timeout=2s，失败跳过刷新）。
3. **DemoDbPoolSaturated 刚性口径 flaky**：纯负载打满受服务端 GIL 与 Docker
   Desktop 端口代理周期停顿影响，瞬时 used 深度抖动（min 可到 2）。口径改为
   1m 平均占用 ≥70% 池容量（慢 SQL 等硬饱和形态 used 恒钉 10，两种口径均触发）。

### 其他配套变更

- demo 观测面扩展：GET /tasks/{id} 加 redis 缓存（TTL 60s）+ 命中/未命中 Counter；
  create_task 携带协议版本参数；worker 增加 redis 门禁 chaos:min_protocol 校验
  （剧本 11 的版本偏斜注入点）；STALE_PENDING gauge
- 告警规则 6 → 8 条：新增 DemoCacheMissSpike、DemoTasksStuckPending（带 scenario label）
- Pumba CLI 坑补充：--duration 必须 < --interval（按 interval 周期重施）；
  --interval 值必须带时间单位（"1s" 而非 "1"）

## Blocked by

03（已解除）
