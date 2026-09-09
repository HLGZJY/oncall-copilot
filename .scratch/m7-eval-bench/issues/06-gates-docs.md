Status: ready-for-agent
Blocked by: 05

# 06 Makefile/CI 回归收口 + 架构回写 + 文档

## 任务

- `make eval`（mock 档进 CI 回归）+ `make eval-real`（真实档 env 开关）
- 架构 §4 第十表回写（如 01 票未回写则此票统一收口）
- CONTEXT.md 新术语核对（评审时应已入表，查漏）；README 评测章节占位（挂图归 M8）

## 验收

- [ ] `make eval` 一键跑通并进 CI
- [ ] 架构 §4 与实际 schema 一致
- [ ] 全量 pytest 只增不减 + ruff 双检绿

## Comments

- 设计引用：G1/G5
