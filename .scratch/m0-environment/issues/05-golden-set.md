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
