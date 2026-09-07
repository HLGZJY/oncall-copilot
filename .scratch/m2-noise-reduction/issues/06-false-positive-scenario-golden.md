Status: resolved
Blocked by:

# 06 误报剧本 false-positive-flap + golden 标注扩展（T6 / D-21 定案）

> **为什么 ready-for-human**：本票扩展 golden 标注 schema（`alert_timeline` 增可选字段 `classification`）并改动 `chaos/scenarios/` 场景库——两者都是 D-12/D-18 冻结过的契约面，按「涉及架构取舍的留 ready-for-human」纪律归人工；方案已在设计文档 G7/D-21 定案，执行内容完全明确，人工放行后即可照做。
> **人工放行**：本次派工即放行（2026-09-07），方案照 G7/D-21 执行；两处契约张力（R2 过渡豁免、category 扩枚举）按推荐解法落地并留痕（见 Comments 与 decisions.md D-21 补记）。

## 任务

- **误报剧本**：`chaos/scenarios/` 新增 `false-positive-flap`：inject 脚本**不注入业务故障**，仅临时下调某条 Prometheus 告警规则阈值使其在正常水位误触发（配置漂移型误报），cleanup 恢复规则 → resolved
- **golden 扩展**：`datasets/golden/dev/` 新增 `false-positive-flap.yaml`（D-18 十字段齐全，三标注字段与 `expected_*` 逐字一致；timeline 条目标注 `classification: false_positive`）；`expected_root_cause` = 规则阈值配置漂移（无真实故障）
- **校验器兼容**：`load_golden_tree` R6 校验兼容可选字段 `classification`（缺省 `incident`，向后兼容——既有 11 剧本零改动应继续通过）
- **holdout 不动**：holdout/ 同步延至 M7 前，M2 期间禁看不变

## 要点

- D-18 纪律：预标注必须有权威源——本剧本的「根因」是规则配置差异，investigation_path 应指向「查规则配置 vs 业务指标正常」的排查链路；标注错 = 评测全错
- 误报剧本的存在意义：验证「0 漏报」的另一半——不冤枉真告警的同时能识别假告警（PRD §7-M2 硬要求）
- 注入方式选「规则阈值下调」而非「伪造 webhook payload」：前者走真实 Prometheus 评估链路，告警是真实产生的（数据真实性口径）

## 验收（可机械判定，实测后回填）

- [x] 注入 → 无业务故障状态下目标告警 firing（Prometheus 真实评估触发，非伪造 payload）；cleanup → resolved（r1 firing 10:28:36→resolved 10:28:51，r2 firing 10:30:36→resolved 10:30:51，均 UTC 2026-09-07，dump 实测；generatorURL 证 firing 时 expr=0.01、恢复后 expr=0.12）
- [x] `false-positive-flap.yaml` 通过 `golden_matches_scenario` 逐字段比对；`load_golden_tree` R6 校验绿（test_golden_tree_cross_validates_against_scenarios 真实数据全树绿）
- [x] 向后兼容：既有 11 个 dev 剧本在缺省 classification（= incident）下校验行为不变（TestAlertEventClassification::test_missing_classification_defaults_to_incident + 全量回归绿）
- [x] holdout/ 目录零改动（git status 可证，本票全程未读写 holdout 内容）

## Comments

- **注入实测记录（2026-09-07，9 容器 compose 栈）**：
  - 阈值下调节点：误报专属规则 `DemoTasksLatencyFlap`（第 9 条，`labels.scenario: false-positive-flap`）监控 /tasks P95，默认阈值 0.12s（基线 4.9 倍裕度）临时下调到 0.01s（基线之下 2.5 倍）。基线实测：轻载 ~2/s 探针下 /tasks P95 稳定 24.6–24.9ms（直方图 25ms 桶），stale_pending=0，无任何告警 firing——保证"无真实业务故障"剧本口径。阈值取 0.12 而非 0.1：避开 DemoHighErrorRate 已用的 0.1，保证 inject/cleanup 的 sed 替换唯一命中
  - reload 方式：Prometheus 容器未开 `--web.enable-lifecycle`，`/-/reload` 不可用；采用 SIGHUP（`docker kill -s HUP oncall-demo-prometheus-1`）触发配置重载，实测生效（promtool check rules 先验）
  - 时间线来源：`collect_run.sh` 采集，fired_at/resolved_at 一律取 deploy/alerts-dump.jsonl 实测值（切片存 `datasets/golden/_raw/false-positive-flap-r{1,2}.jsonl`），未编造；两轮均为注入→firing→cleanup→resolved 完整闭环
- **环境整备记录**：基线测量初期误用 64 连接 demo-load（"预热负载"实测会压出 3 条真实告警、/tasks P95≈0.87s），且重载阶段灌入 ~3 万任务致 broker 积压 2.6 万、DemoTasksStuckPending 残留 firing——已整备（清 broker 队列 + pending 补偿为 failed 终态，同 protocol-mismatch cleanup 补偿先例）后重建健康基线再采集
- **R2 过渡豁免落地方式**：schema.py 新增显式常量 `HOLDOUT_SYNC_PENDING: frozenset[str]`（含 false-positive-flap），清单内 slug 允许 dev 单边（跳过 R2 + R3-holdout/R4），R1/R3-dev/R6 全部照跑；**不放宽 R2 为警告**（弱化纪律，禁选）；holdout 零改动。已写入 M7 前待办：holdout 同步完成后清空该常量，成对硬约束原样恢复
- **category 扩枚举**：`ScenarioCategory` 扩第七类「误报类」（六类对误报剧本无处安放，D-12 契约面）；`test_catalog_covers_all_six_categories` 同步更名 `test_catalog_covers_all_categories`（6→7 类）；decisions.md D-21 同条补记
- **门禁实测**：262 passed / 4 skipped（基线 250 → 净增 12：schema D-21 组 5 + R2 豁免组 7）；coverage 97.29%；ruff check / format 全绿。schema.py 压注释至 299 行守住 C6 ≤300 行守卫（拆分留给后续真实压力，本票不扩契约文件体积）
