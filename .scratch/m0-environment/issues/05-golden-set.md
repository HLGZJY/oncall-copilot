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
