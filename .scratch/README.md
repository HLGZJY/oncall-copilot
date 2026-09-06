# .scratch/ — 本仓库的 issue 追踪器

Issues 与 spec 以 markdown 文件形式存在这里，并**纳入版本控制**（所以 issue 变更会出现在提交历史里，这是刻意的：可追溯）。

## 约定

- 一个 feature 一个目录：`.scratch/<feature-slug>/`
- spec：`.scratch/<feature-slug>/spec.md`
- issue：**每个 ticket 一个文件**，`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 开始编号（`<NN>` 就是 issue ID，可直接用 `/implement 03` 引用）
- 状态：每个 issue 文件顶部有 `Status:` 行，取值见 `docs/agents/triage-labels.md`
- 阻塞：顶部 `Blocked by: 01, 02` 行，列出的文件全部 `resolved` 后才解除阻塞
- 讨论：追加到文件末尾的 `## Comments` 下

## 完整约定

见 `docs/agents/issue-tracker.md`（含 wayfinding 的 map / claim / resolve 操作）。
标签词汇见 `docs/agents/triage-labels.md`。

## 为什么放在仓库里而不是用 GitHub Issues

本仓库当前无远端，且不希望 issue 依赖外部服务。更重要的是：**issue 与代码同版本控制**后，一次提交可以同时携带"实现了什么"和"为什么做这个决定"，半年后回溯时上下文不会丢。
