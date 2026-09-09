# m7-eval-bench · Spec

M7 评测台（剧本 runner + 指标矩阵 + 模型对比 + 回归）（W6，PRD §7-M7）。**权威设计**：`docs/design/m7-eval-bench-design.md`（status: **draft**，2026-09-09 草案完成；G1–G8 决策点已预填推荐解，**待用户逐条拍板**，定案登记 `decisions.md` D-58+）。

> **派工冻结**：G1–G8 评审通过前本目录 issue 一律 `needs-triage`，**不得开工**（设计票零写码纪律）。

## 目标

对黄金集剧本全自动跑 N 遍产出可回归的指标矩阵（Top-1/Top-3 命中、步数、耗时、成本、失败模式），≥2 LLM 模型对比（env 切换面零硬编码），`make eval` 进回归；判对错两级 + 失败模式强制归类不丢弃；复用出口与 escalated 按 D-57/D-56 口径单列。

## 关键口径（设计草案推荐解，评审后为准）

- 判对错：规则匹配全量兜底 → LLM-as-judge 复核（契约 mock 冻结，真实 judge 是 key 门槛票）→ 人工抽检 20%
- N=3 报均值±极差；不稳定行标 `unstable` 单列
- 失败模式：消费循环六值 → 规则兜底 → unknown 强制人工归类禁丢弃
- 复用出口不计入命中分母（复用命中率单列）；escalated 不计失败（人工标注通道）
- 防泄漏：few-shot 只取 dev 且排除 3 验证剧本；holdout 最终评测前禁读
- mock-first：全部单测零真实 LLM 调用；真实档 `ONCALL_RUN_M7_EVAL=1` + key 门槛票单独拍板

## 任务序列

| Issue | 任务 | Blocked by | 就绪态 |
|---|---|---|---|
| 01 | eval_runs 第十表 + eval 模块骨架 + golden 加载器（防泄漏守卫） | — | needs-triage |
| 02 | 剧本 runner（装配 + mock/真实双档） | 01 | needs-triage |
| 03 | 判对错管线（规则匹配 + judge 契约 mock） | 01 | needs-triage |
| 04 | 指标核算（复用 stats.py + 五列 + 复用/escalated 单列） | 03 | needs-triage |
| 05 | N 遍矩阵 + 模型 profile 矩阵 + 报告双产物 | 02,04 | needs-triage |
| 06 | Makefile/CI 收口 + 架构回写 + 文档 | 05 | needs-triage |
| 07 | 真实评测（key 门槛票 + 活栈门槛） | 06 | needs-triage（附门槛） |
