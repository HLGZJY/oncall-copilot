Status: ready-for-human
Blocked by:

# 06 误报剧本 false-positive-flap + golden 标注扩展（T6 / D-21 定案）

> **为什么 ready-for-human**：本票扩展 golden 标注 schema（`alert_timeline` 增可选字段 `classification`）并改动 `chaos/scenarios/` 场景库——两者都是 D-12/D-18 冻结过的契约面，按「涉及架构取舍的留 ready-for-human」纪律归人工；方案已在设计文档 G7/D-21 定案，执行内容完全明确，人工放行后即可照做。

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

- [ ] 注入 → 无业务故障状态下目标告警 firing（Prometheus 真实评估触发，非伪造 payload）；cleanup → resolved
- [ ] `false-positive-flap.yaml` 通过 `golden_matches_scenario` 逐字段比对；`load_golden_tree` R6 校验绿
- [ ] 向后兼容：既有 11 个 dev 剧本在缺省 classification（= incident）下校验行为不变（回归测试）
- [ ] holdout/ 目录零改动（git status 可证）

## Comments

-
