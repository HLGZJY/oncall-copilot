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
