Status: ready-for-human
Blocked by: 04

# 05 黄金集生成（每剧本 ×3 run）

## 任务

逐剧本执行 ×3 次，记录生成 `datasets/golden/<slug>.yaml`：`runs[].alert_timeline[]`（alert_name/labels/fired_at/resolved_at）+ 触发/恢复时间 + **预标注根因 + 标准排查路径 + 标准处置**；按 **dev/holdout 双集隔离**分目录（架构文档 §6：dev 可调参，holdout 终评专用、调参禁看）。

## 为什么 ready-for-human

预标注的"根因/排查路径/处置"是 M7 的判分基准——标注错 = 评测全错。执行可由 Agent 辅助（跑注入、采集告警时间线），但**标注准确性的人工核对权在人**。建议流程：Agent 采集 → 人核对标注 → 双签回填。

## 验收

- [ ] 每剧本 1 份 YAML、≥3 条 run 记录，过校验器
- [ ] dev/holdout 分目录，holdout 内容在调参期不被引用
- [ ] 至少抽 1 个剧本由人工核对标注准确性（核对记录写入 Comments）

## Blocked by

04

## Comments

### 2026-09-06 Agent 采集完成（首批 4 剧本）——待人工抽核

**已完成**：
- 校验器 TDD：`load_golden_tree` 双集目录树校验（commit `818d75b`，红灯 10 用例 → 绿灯，coverage 97.86%）
- 现场采集 12 次闭环（注入→firing→cleanup→恢复，均实测确认）：11-protocol-mismatch / 04-downstream-timeout / 05-packet-loss / 06-db-deadlock 各 ×3 轮
- 草稿落盘：`datasets/golden/dev/{slug}.yaml`（r1+r2）+ `datasets/golden/holdout/{slug}.yaml`（r3），全部过 `load_golden_tree`
- 时间线纪律：fired_at/resolved_at 全部取 `deploy/alerts-dump.jsonl` 实测值（Alertmanager startsAt/endsAt 按指纹配对），原始切片留档 `_raw/`；无任何编造时刻
- 采集工具：`datasets/golden/tools/collect_run.sh`（单轮闭环驱动）+ `build_yaml.py`（切片→YAML，按 expected_alerts 过滤）

**待人工（本 issue 保持 ready-for-human 的原因）**：
1. 抽核 ≥1 个剧本的预标注准确性（root_cause / investigation_path / remediation 三字段），核对记录追加到本 Comments
2. 双签后把草稿头部 `标注状态: DRAFT` 改为回填确认；issue 置 resolved 由人执行

**备注**：
- rules.yml 的 `scenario` 标签是静态来源标注（如 DemoApiGwHighLatency 固定标 cpu-spike），不能作为告警归属判定——采集判定只看 alertname ∈ expected_alerts
- 07 号遗留两个 idle 观察容器（vibrant_chatterjee / objective_germain，仅 `tail -f /dev/null`）不注入任何东西，可手动清理
- 剩余 7 剧本（01/02/03/07/08/09/10）采集顺延，同一套工具直接复用

### 2026-09-07 双盲复核（Agent 独立重推，待人工裁决）

**方法**：先只读 11 个 inject.sh/cleanup.sh 独立推导根因（不看标注思路），再与标注对比；
脚本核对 4 项——①golden vs scenario.yaml 逐字关系 ②timeline vs expected_alerts 集合差
③_raw 切片 + 全量 dump 对缺口告警终审 ④holdout 与 dev 一致性。

**结论 1（通过）**：11 剧本 `root_cause`/`remediation` 与 `scenario.yaml` 的 `expected_*`
**逐字一致**（此前采集 Comments 未明说复制关系，特此钉死）；root_cause 机理审查 11/11
与注入脚本证据相符（cpuset+stress-ng+cgroup 注入 / LOCK TABLES WRITE+SLEEP / memory.max
60% OOM PID1 / netem 700ms / 25% loss RTO 重传 / 锁环+supremum+死锁检测 OFF /
16>9 task/s 净堆积 / 192 keep-alive 404 必 miss / SIGKILL 137 杀消费端 / FLUSHDB db0
50ms 间隔不碰 broker db1 / 门禁 v2 语义层静默跳过），时间线因果序全部自洽。

**发现 1（P1，需修口径）**：3 个剧本预标注声称的级联告警**实测未发生**（全量 dump 时间窗
终审 0 firing，排除切片丢数据）：
- `queue-backlog`：root_cause 称"实测 DemoTasksStuckPending 随之触发"——实测 0 次
- `downstream-timeout`：expected 5 条告警实测只出现 3 种（QueueDepthHigh / StuckPending
  未触发），root_cause"队列堆积/任务滞留级联"系推演非实测
- `packet-loss`：root_cause 称"连带队列堆积"——QueueDepthHigh 实测 0 次
- 机理裁决：DB 变慢同时拖慢提交（POST 写 DB）与消费两侧，净堆积未越 for:2m 阈值，
  级联不成立。**建议**：修 `scenario.yaml` 的 expected_alerts / expected_root_cause
  措辞（golden 逐字同源会同步），或在标注中注明"该级联为推演，实测未发生"。

**发现 2（P2，需修 path）**：`investigation_path` 两组疑似跨剧本复用且与机理不匹配：
- `cache-avalanche` 第 1 步"查 up 指标与容器退出码"——缓存故障无进程死亡（up 全绿、
  无退出码），应从 miss 率 / redis 写入行为切入
- `pool-exhaustion` 第 1/2 步"提交/消费速率对比、队列深度"——同步读负载无队列环节，
  应从池占用 + QPS + 无慢查询切入
建议 11 个剧本 path 逐剧本定制（M7 判分依赖此路径，误导性步骤会白白消耗 Agent 步数）。

**发现 3（P2，M7 判分注意）**：timeline labels 里的 `scenario` 标签是 rules.yml 静态
来源标注（如 db-deadlock 时间线里 scenario=slow-sql）——M7 归属判定只能用
alertname ∈ expected_alerts，禁用该 label；已验证 golden 原样保留实测 labels 未篡改
（符合"不编造"纪律）。

**发现 4（记录，非错误）**：2 处 `resolved_at: null`（packet-loss r1 HighLatency /
pool-exhaustion r1 CacheMissSpike）经 _raw + 全量 dump 终审确认为 dump 未收到对应
resolved 通知，属如实记录；M7 配对时该 run 的恢复以 `recovered_at` 为准。

**holdout**：11 文件标注与 dev 全一致，r3 为独立轮次。

**待人工**：发现 1/2 的修正裁决 + ≥1 剧本抽核双签。本 issue 保持 ready-for-human。

### 2026-09-06 Agent 采集完成（第二批 7 剧本）——11 剧本全部采集完毕，待人工抽核

**已完成**：
- 剩余 7 剧本各 ×3 轮（21 次闭环，单轮 2.5–9min，合计 ~97min）：07-queue-backlog / 08-pool-exhaustion（负载类，先跑）→ 01-cpu-spike / 09-process-killed（Pumba 类）→ 02-slow-sql / 03-oom-kill（脚本类）→ 10-cache-avalanche（50ms FLUSH 间隔，最后跑）
- 全部按建议顺序执行，逐轮确认 ALERTS 归零后再进下一轮；全程未 down compose（9 容器保持运行）
- 草稿落盘：`datasets/golden/dev/{slug}.yaml`（r1+r2）+ `holdout/{slug}.yaml`（r3），11 剧本全部过 `load_golden_tree` 校验
- 时间线纪律不变：fired_at/resolved_at 取 alerts-dump.jsonl 实测值按指纹配对，原始切片留档 `_raw/`；无编造时刻
- 提交分 4 批：`5e25268`（07/08）→ `c67ae56`（01/09）→ `82bf4cd`（02/03）→ `b0d12c8`（10，收官）
- 无 FAIL 轮次（resolved 等待轮询 7f41879 生效，未见 6min 超时）

**备注**：
- 02 号 cleanup 时 mysql `ERROR 1094 Unknown thread id` 系 KILL 竞态噪声（持锁会话已自行结束），不影响告警配对
- 10 号切片有 2 行并发截断坏行，解析器按纪律跳过，配对不受影响
- 09 号单轮 ~9min（worker SIGKILL 后扩容 + 消化积压），属固有恢复时长
- **待人工**：抽核 ≥1 剧本标注（root_cause / investigation_path / remediation），核对记录追加到本 Comments；双签后 DRAFT→回填确认；issue 置 resolved 由人执行
