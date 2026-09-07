---
title: "黄金集标注校准设计（P1 口径修正 + P2 排查路径定制）"
summary: "修复 M0-05 双盲复核发现的预标注与实测不符（3 剧本级联未发生）与 investigation_path 跨剧本复用问题，含 schema 契约变更与校验器增强"
source: docs/design/feature-design-template.md
status: implemented
updated: 2026-09-07
read_when: 处理 M0-05 黄金集 P1/P2 发现、改动 scenario.yaml 或 scenarios/schema.py 前
---

# 黄金集标注校准（P1 口径 + P2 排查路径）

## status

`draft`（草案，讨论中）→ `reviewed`（评审通过，可开工）→ `implemented`（已落地，实测数据已回填）

- **当前状态**：`implemented`（2026-09-07 方案 A 裁决后当日落地；D-18 已登记）
- **评审人 / 评审日期**：用户裁决方案 A（2026-09-07）
- **关联 issue**：`.scratch/m0-environment/issues/05-golden-set.md`（Comments 2026-09-07 双盲复核，发现 1/2/3）
- **触发**：issue 06 receiver 切换的前置门槛；本设计评审通过后 M0-05 的修正路径即定，人工只需裁决 + 双签

## 目标

- **背景 / 触发原因**：M0-05 双盲复核（提交 5b466e3）证实：①3 个剧本预标注声称的级联告警在全量 dump 时间窗内 0 次 firing（P1）；②`investigation_path` 两组跨剧本复用且与故障机理不匹配（P2）；③timeline 的 `scenario` 标签是静态来源标注，M7 归属判定禁用（P2 纪律，需落文档）。
- **要解决的问题**：黄金集是 M7 评测台的判分基准——标注错 = 评测全错。本设计把预标注收敛到"实测可证"状态，并修掉让问题溜进来的两个结构性缺口：investigation_path 无权威源、校验器缺 timeline 与 expected_alerts 的一致性规则。做完后 M0-05 具备 resolved 条件，issue 06 前置门槛解除。
- **不做的事（Non-goals）**：
  - 不动 M0 告警规则（`rules.yml` 的阈值 / `for: 2m`）——那会改变全部 11 剧本的告警基线，需全量重采；
  - 不重采任何已采集的 run 时间线（fired_at/resolved_at 实测值不动，问题只在标注文字与 expected 集合）；
  - 不做 M7 评测台本身（判分逻辑属 M7，本设计只保证基准数据可信）；
  - 不推进 issue 06 receiver 切换。

## 技术方案

**一句话概括**：P1 采「修口径对齐实测」（方案 A），把 3 剧本的 expected_alerts 与 expected_root_cause 收敛到 dump 可证的事实；P2 采「权威源前移」，在 scenario.yaml 新增 `expected_investigation_path` 字段作为唯一权威，golden 逐字复制并纳入校验器强制一致——与 root_cause/remediation 已有的逐字同源模式对齐。

### P1 方案取舍（需用户裁决）

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A 修口径** | expected_alerts 收敛到实测触发集合；expected_root_cause 删除/改写"级联"措辞为实测事实 | 零重采、当天完成、符合"实测回填不得虚构"纪律 | 3 剧本信号变单一，M7 难度略降 | **推荐，本设计主体** |
| B 增强注入 | 调注入强度/时长让净堆积真实越过 `for: 2m` 阈值，级联真实发生，3 剧本 ×3 runs 重采 | 数据集更真实、多信号 | 需重采（约 45min+）且"调到什么程度能触发"要试凑多轮；为凑告警调注入可能稀释剧本的表现纯度 | 列为 M2+ 可选增强项，M7 评测台有初版后再评估收益 |
| C 双轨标注 | expected_alerts 拆 observed/inferred 两层，golden 保留实测 + 记录推演 | 不丢推演信息 | D-12 冻结契约拆字段，M7 判分还要决定用哪层，复杂度最高、收益最低 | 不推荐 |

机理裁决依据（复核已证）：DB 变慢同时拖慢提交与消费两侧，净堆积未越 `for: 2m` 阈值，级联不成立——这不是采集缺陷，是预标注推演超前于实测。方案 A 不是"降低标准"，是把基准钉回证据。

### P2 结构性修复：investigation_path 权威源前移

**根因**：`root_cause`/`remediation` 在 scenario.yaml 有 expected_* 权威源、golden 逐字复制（复核证实 11/11 一致，零漂移）；`investigation_path` 只在 golden 侧手写，无权威源 → 标注时跨剧本复制无人拦截。修复 = 补上同一条同源链路：

1. **scenario.yaml 新增 `expected_investigation_path: list[str]`**（每剧本 ≥1 条），11 个剧本逐剧本定制；
2. **golden 的 `investigation_path` 逐字复制**该字段（dev/holdout 双集同步）；
3. **校验器升级** `golden_matches_scenario`：从"只比 scenario 名"升级为逐字段一致性校验（root_cause / remediation / investigation_path 逐条），并在 `load_golden_tree` 增加 **R6：timeline 的 alertname 集合 ⊆ expected_alerts**——P1 正是从这条缺失的规则漏进来的。

定制原则（防再次复用的判据）：**path 第 1 步必须指向 inject.sh 直接产生的、第一个可独立观测的信号**，不允许以上游系统视角或上帝视角开场；每条步骤提到的工具/指标必须在该剧本的观测环境里真实存在。

11 剧本切入点（复核已溯源到 inject.sh，实施时展开为完整步骤）：

| 剧本 | 故障机理（inject.sh 溯源） | path 第 1 步切入点 |
|---|---|---|
| 01-cpu-spike | cpuset 限核 + stress-ng 打满 | CPU 使用率/限流指标 |
| 02-slow-sql | LOCK TABLES WRITE + SLEEP | MySQL 慢查询/锁等待 |
| 03-oom-kill | memory.max 60% 触发 OOM PID1 | 容器内存用量 + OOM 事件 |
| 04-downstream-timeout | netem 700ms 延迟注入 | 下游 P99 延迟 |
| 05-packet-loss | netem 25% 丢包（RTO 重传） | 重传计数/连接超时 |
| 06-db-deadlock | 锁环 + 死锁检测 OFF | InnoDB 锁等待/事务图 |
| 07-queue-backlog | 16 task/s 灌入 vs 约 9 task/s 消费 | 生产/消费速率对比 |
| 08-pool-exhaustion | 同步读负载打满连接池 | 池占用 + QPS（无慢查询佐证） |
| 09-process-killed | SIGKILL 杀消费端（137） | 容器退出码 + 副本数 |
| 10-cache-avalanche | FLUSHDB db0（50ms 间隔，不碰 broker db1） | 缓存 miss 率跳变 |
| 11-protocol-mismatch | 门禁 v2 语义层静默跳过 | 关键错误日志（无指标异常） |

### 设计的模块

| 模块 | 动作（新增/修改） | 职责 | 关联里程碑 |
|---|---|---|---|
| `chaos/scenarios/*/scenario.yaml`（3 个，P1） | 修改 | expected_alerts 收敛到实测、expected_root_cause 去推演措辞 | M0 |
| `chaos/scenarios/*/scenario.yaml`（11 个，P2） | 修改 | 新增 expected_investigation_path，逐剧本定制 | M0 |
| `datasets/golden/{dev,holdout}/*.yaml`（22 文件） | 修改 | 标注三字段与 scenario.yaml 逐字同步；实测 timeline 不动 | M0 |
| `src/oncall/scenarios/schema.py` | 修改 | ScenarioSpec/GoldenSet 契约扩展 + 一致性校验 + 树级 R6 | M0（服务 M7） |
| `tests/unit/test_scenarios_schema*.py` | 修改 | 新契约与 R6 的 TDD 用例（含 P1 回归样本：timeline 含 expected 外告警必须报错） | M0 |
| `docs/design/decisions.md` | 修改 | 登记 D-18（D-12 契约扩展 + 一致性规则） | — |

### 数据模型变更

| 变更项 | 类型（新增/修改/删除） | 说明 | 迁移方式 |
|---|---|---|---|
| `ScenarioSpec.expected_investigation_path` | 新增 | `list[str]`，min_length=1；D-12 九字段契约 → 十字段，过 decisions.md（D-18） | pydantic `extra="forbid"` 下旧文件缺字段直接报错，11 个 scenario.yaml 同批补齐后过校验 |
| `GoldenSet` 一致性校验 | 修改 | `golden_matches_scenario` 逐字段比对（三标注字段逐字 ==） | 纯校验逻辑，无持久化迁移 |
| `load_golden_tree` R6 | 新增 | timeline alertname 集合 ⊆ 对应剧本 expected_alerts | 需先完成 P1 口径收敛，否则现存 dev/holdout 即报错——收敛与 R6 同批落地互为前提 |

### API变更

无（schema 模块纯离线，不涉 REST/工具注册表；Agent 循环不受影响）。

## 验收标准

- [x] P1：3 剧本（queue-backlog / downstream-timeout / packet-loss）的 expected_alerts 与全量 dump 实测触发集合**完全一致**（分别删 DemoTasksStuckPending×1 / DemoQueueDepthHigh+DemoTasksStuckPending / DemoQueueDepthHigh）；expected_root_cause 已改写为实测口径（含 StuckPending `stale pending >5 for 2m` 规则依据、推演级联不成立声明）——脚本校验 dev/holdout×3 与 scenario.yaml 逐字一致 ✓
- [x] P2：11 个 scenario.yaml 均含 expected_investigation_path 且逐剧本不同（第 1 步全部指向 inject.sh 首个可观测信号）；22 个 golden 文件三字段与权威源逐字一致（脚本断言，非目测）✓
- [x] 校验器：R6 + 三字段一致性校验落地（`_cross_check_slug`），负向用例含 P1 复现样本（timeline 出现 expected 外告警报错）、标注漂移样本、缺剧本样本；全量 **pytest 143 passed / 4 skipped，coverage 95.89%**，ruff check + format 绿 ✓
- [ ] M0-05 Comments 追加修正记录；人工抽核 ≥1 剧本 + 双签后 issue 置 resolved（人的裁决权不因自动化旁路）——**待人工**
- [x] holdout 与 dev 标注仍逐字一致（R4 防复制规则继续生效，run 时间线零改动——git diff 可核时间线字段未动）✓

**实施备注**：①`yaml.safe_dump` 折行中文长文本会追加 `...` 文档结束标记（非法）且折行处读回引入空格——P1 三剧本 root_cause 块最终以**不折行单行**落盘（width=10⁹），修正脚本曾两轮迭代；②校准脚本放 `.scratch/tmp/` 用完即删，过程结论以本节与 M0-05 Comments 为准。

## 依赖

- **前置依赖**：M0-05 采集已全部完成（5e25268→b0d12c8）；双盲复核结论已提交（5b466e3）；`src/oncall/scenarios/schema.py` 校验器已存在（commit 818d75b）。本设计与 issue 06 可并行评审，但 **M0-05 置 resolved 必须先于 issue 06 开工**（黄金集靠 dump 采集，G6 约束）。
- **数据 / 环境依赖**：`deploy/alerts-dump.jsonl` 全量 dump（P1 实测口径的唯一证据源，已终审）；`datasets/golden/_raw/` 切片留档。无新增采集。
- **后续影响**：M7 评测台的判分基准直接消费本设计产物；D-18 契约变更后，任何新增剧本在 scenario.yaml 阶段就强制携带定制排查路径，P2 类问题从源头封死。
