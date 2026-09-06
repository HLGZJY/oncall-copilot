---
title: "Issue tracker：本地 Markdown"
summary: "本仓库用 .scratch/ 下的 markdown 文件作为 issue 追踪器，含目录约定、状态行、阻塞边与 wayfinding 操作"
source: mattpocock/skills → skills/engineering/setup-matt-pocock-skills/issue-tracker-local.md
status: active
updated: 2026-09-06
read_when: 要新建 issue / 查 issue 状态 / 用 wayfinder 派工时
---

# Issue tracker：Local Markdown

本仓库的 issues 与 specs 以 markdown 文件形式存放在 `.scratch/`，**纳入版本控制**。

> 选型理由：仓库无远端、不希望依赖外部服务，且 issue 与代码同版本控制后可完整追溯"为什么做这个决定"。
> 若日后接入 GitHub Issues，只需替换本文件并把 `triage-labels.md` 的右列改成真实标签即可，其余约定不变。

## 约定

| 项 | 规则 |
|---|---|
| feature 目录 | 一个 feature 一个目录：`.scratch/<feature-slug>/` |
| spec | `.scratch/<feature-slug>/spec.md` |
| issue 文件 | `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 编号，**一 ticket 一文件**，绝不用合并的 tickets.md |
| 状态 | 文件顶部 `Status:` 行，取值见 [`triage-labels.md`](triage-labels.md) |
| 阻塞 | 顶部 `Blocked by: 01, 02`，列出的文件全部 `resolved` 后才解除 |
| 讨论 | 追加到文件末尾的 `## Comments` 下 |

`<NN>` 就是真实 issue ID，可以直接说"实现 03"，不必复述长标题。

## 当某个 skill 说"发布到 issue tracker"

在 `.scratch/<feature-slug>/` 下新建文件（目录不存在则创建）。

## 当某个 skill 说"取回相关 ticket"

读取对应路径的文件。用户通常会直接给出路径或 issue 编号。

## Wayfinding 操作

供 `/wayfinder` 使用。**map** 是一个文件，每个 ticket 一个**子文件**。

- **Map**：`.scratch/<effort>/map.md`（Notes / Decisions-so-far / Fog 三段）
- **子 ticket**：`.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 编号，正文里是待回答的问题
  - `Type:` 行记录类型：`research` / `prototype` / `grilling` / `task`
  - `Status:` 行记录：`open` → `claimed` → `resolved`
- **阻塞**：顶部 `Blocked by: NN, NN`。列出的全部 `resolved` 后解除阻塞
- **Frontier（前沿）**：扫描 `.scratch/<effort>/issues/`，找出 open + 未阻塞 + 未认领的文件，编号小的优先
- **Claim（认领）**：动手前先写 `Status: claimed` 并保存
- **Resolve（解决）**：把答案追加到 `## Answer` 下，写 `Status: resolved`，再往 `map.md` 的 Decisions-so-far 追加一条上下文指针

## 与里程碑的关系

本项目按 M0–M9 推进，因此 feature-slug 建议直接用里程碑或模块名，例如 `.scratch/m3-investigation-loop/`。
