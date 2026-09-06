Status: ready-for-agent
Blocked by: 05

# 06 一键复现与 M0 收尾

## 任务

A. 根 compose 整合 + Makefile 入口：`make up` / `make demo-load` / `make inject SCENARIO=<slug>` / `make down`。
B. README 快速开始：新人按文档可复现"注入 → 告警"全流程。
C. 数据质量自查并回填设计文档验收节（`docs/design/m0-environment-design.md`）：每个剧本至少 1 个指标可检异常？关键错误在 Loki 日志里查得到？拓扑可从指标 label 还原？
D. 全部验收项打勾后，设计文档 `status: reviewed → implemented`，实测数据回填。

## 验收（机械判定）

- [ ] 干净环境下 `make up` 一键拉起，`make inject SCENARIO=01-cpu-spike` 全流程可复现
- [ ] README 快速开始段可用（无内部黑话、无缺失步骤）
- [ ] 数据质量自查 3 问逐剧本有答案，回填设计文档
- [ ] 全量 pytest 通过；设计文档状态翻 implemented

## Blocked by

05

## Comments

### 2026-09-06 部分开工（不依赖黄金集的部分）——黄金集采集并行中，勿合并不置 resolved

**本次范围**：任务 A/B + 任务 C 的自查结论（下方）；任务 D（验收打勾 + status 翻 implemented）**不做**，等 issue 05 黄金集齐全。采集会话先合并，本分支（`issue06-m0-wrapup`，worktree）后合并。

**① 根 compose 整合梳理（对运行中栈只读检查，未 down）**：
- `docker-compose config -q` 通过；9 容器全部 Up（api-gw/worker/prometheus/alertmanager/alert-dumper/grafana/loki/redis/mysql），带 healthcheck 的 3 个均 healthy
- `GET /health` → 200 `{"status":"ok","db":"up","redis":"up",...}`；Prometheus targets：demo-api-gw / prometheus 均 up，8 条规则已加载
- Loki `/ready` 返回 503 是 Loki 3.5 + docker driver 结构化元数据告警导致的 readiness 探针误报——**查询链路实测健康**（labels/query_range 均 200，能取到日志流），非阻断项
- 审查结论：单文件 compose 已覆盖全栈（demo 3 + 遥测 3 + 告警 3），服务间依赖经 healthcheck 编排，无需拆分观测栈 compose 文件；loki-url 走宿主端口 127.0.0.1:3100 的插件限制已在注释中登记

**② Makefile 入口**：新增根 `Makefile`（up/down/demo-load/inject/cleanup/collect-run/test/help）+ `chaos/tools/demo-load.sh`（64 keep-alive 连接 POST /tasks 温和负载，容器化探针）。COMPOSE 自动探测 compose 插件与 standalone（本机实测命中 standalone v5.5.0）；SCENARIO 双口径（目录名/slug）解析逻辑已实测 5 例全对。**本机无 make**，recipe 逐条在 bash 实测替代（同文件并行 Edit 丢改动的老坑未涉足，全部串行）。demo-load/inject 未做实跑，避免干扰采集会话在跑的队列类剧本。

**门禁口径（合并前须知）**：pytest 全绿（58 用例，coverage 97.67%）；ruff 在本次改动范围（src/tests/demo/chaos）全绿。`ruff check .` 全仓口径在 HEAD 有 10 处报错，**全部位于 `datasets/golden/tools/build_yaml.py`（issue 05 采集会话在途文件，本分支按指令未触碰）**——采集会话合并前应修复该文件 lint，之后 `make test` 全仓全绿。

**③ README 快速开始**：已改写为可复现口径（make 全流程 + 端口/URL + 剧本库与告警映射说明）；黄金集表述为「分批采集中，进度见 issue 05」；演练录屏留占位。

**④ 数据质量自查（m0-execution 三问，11 剧本）**：

| 剧本 | ①指标可检异常（expected_alerts → 证据指标） | ②关键错误 Loki 可查 | ③拓扑可从 label 还原 |
|---|---|---|---|
| 01-cpu-spike | ✅ DemoApiGwHighLatency ← request_duration P95（非 DB 路径）0.0095→0.091s 实测 | ⚠️ 指标型故障：Loki 无错误行属预期，探针请求流可查 | ✅ job/instance + endpoint |
| 02-slow-sql | ✅ TasksHighLatency/DbPoolSaturated/HighErrorRate，池钉 10 实测 | ✅ 实测 Loki 命中 `pymysql 1205 Lock wait timeout` traceback（6h 窗口 200 行） | ✅ |
| 03-oom-kill | ✅ DemoApiGwDown ← up{job="demo-api-gw"}==0 | ⚠️ OOM 判定在容器层（exit 137/OOMKilled，docker inspect）；Loki 侧信号为 api-gw 日志流断流 + 末尾日志 | ✅ up{job,instance} |
| 04-downstream-timeout | ✅ 5 条告警全 firing 实测（issue 05 采集 12 次闭环含此剧本） | ✅ 超时异常行可查 | ✅ |
| 05-packet-loss | ✅ 4 条告警全 firing 实测（同上，×3 轮） | ✅ 请求超时/重试日志可查 | ✅ |
| 06-db-deadlock | ✅ 12 INSERT 锁等待、池打满实测 | ✅ 同 02，1205 锁等待 traceback 实测在库 | ✅ |
| 07-queue-backlog | ✅ demo_queue_depth 实测 1374 条；**当前栈上 DemoQueueDepthHigh firing 即采集会话在跑本剧本** | ✅ worker 消费日志流（compose_service=worker） | ✅ queue_depth→Redis broker 依赖 |
| 08-pool-exhaustion | ✅ demo_db_pool_used 钉 10（1m 均值 ≥70% 口径）实测 | ✅ 404 读流量 + 池等待日志可查 | ✅ pool_used/pool_size→MySQL 依赖 |
| 09-process-killed | ✅ QueueDepthHigh + StuckPending（SIGKILL worker 后积压）实测 | ✅ 前后两个 worker 容器实例经 container_name 标签区分 | ✅ container_name 区分容器代际 |
| 10-cache-avalanche | ✅ demo_task_cache_operations_total{result="miss"} ~160/s 实测 | ✅ miss 风暴 + DB 读放大日志可查 | ✅ result label 区分 hit/miss |
| 11-protocol-mismatch | ✅ demo_tasks_stale_pending>5 **唯一**信号（无 5xx 无资源异常——语义层设计点的正向验证） | ⚠️ worker 消费日志"看起来正常"正是该剧本特征；业务异常只落在业务指标，日志侧不可判 | ✅ stale_pending→worker 管线 |

**三问总结论**：① 全部 11 剧本各有 ≥1 个指标可检异常（`expected_alerts` ⊆ rules.yml 8 条规则，test_scenario_catalog 守卫覆盖）；② 9 剧本 Loki 可查关键错误/流量证据，2 个例外如实登记——03 的判定信号在容器层（OOMKilled）、11 的日志"正常"属语义层设计预期；③ 拓扑还原口径 = Prometheus job/instance + Loki compose_service/container_name + 指标语义依赖边（db_pool→MySQL、queue_depth→Redis broker、stale_pending→worker），可还原 api-gw→worker→MySQL/Redis 链路；局限如实登记：MySQL/Redis 自身未装 exporter，以应用侧代理指标呈现。

**回填设计文档的方式**：本表结论待黄金集 ×3 采集齐全后（issue 05 resolved）随任务 D 一起压入 `docs/design/m0-environment-design.md` 验收节，避免中途改文档与采集会话冲突。

**遗留给任务 D 的清单**：设计文档验收项统一打勾 + status reviewed→implemented；演练录屏录制（README 已留占位）；issue 06 置 resolved。
