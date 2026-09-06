---
title: "提交、评审与 Git 护栏"
summary: "提交格式、code-review 双轴流程、高危 git 命令清单、诊断类提交纪律"
source: ~/.workbuddy/开发规范与工作流程.md 第5章 + AGENTS.md 提交规范
status: active
updated: 2026-09-06
read_when: 每次提交前、做 code-review 时
---

# 提交、评审与 Git 护栏

## 提交格式

```
<type>(<scope>): <中文简述>

<body：为什么，不是做了什么>
```

- type：feat / fix / docs / chore / refactor / test / perf / ci；scope 用模块名（`M3-调查循环`）
- 带 issue 引用（`Closes #12`），code-review 靠它反查规格来源
- **诊断类提交必须写明最终被证实的那个假设**

## code-review 双轴

- Standards 轴（是否符合规范）∥ Spec 轴（是否实现了想要的）——分开报告，不合并不重排
- 基准点用 `git diff <fixed-point>...HEAD`（三点号）；坏引用/空 diff 在派发前失败
- 规格来源顺序：提交信息 issue 引用 → 用户路径 → docs/.scratch 匹配文件 → 问用户
- Standards 轴永远携带 Fowler 坏味道基线（12 条，见开发规范 5.2）；每条都是判断题不是硬违规

## Git 护栏（本环境无 hooks，靠规则替代）

高危清单：`push --force` / `reset --hard` / `clean -f` / `branch -D` / `checkout .` / `restore .`
执行前：列出影响范围 → 用户确认；不 `--no-verify`；冲突按意图解，永不 `--abort`。

## CRLF

`.gitattributes`（`* text=auto eol=lf`）+ `git config core.autocrlf false`（Windows 既有环境约定）。
