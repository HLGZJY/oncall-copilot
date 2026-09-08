---
title: "docs/ 知识库索引"
summary: "OnCall Copilot 细节知识的分层结构：architecture / conventions / design / plans / reference / agents"
source: 本仓库约定（2026-09-06 建立）
status: active
updated: 2026-09-09
read_when: 任何会话找不到该去哪找细节知识时
---

# docs/ — 结构化知识库

> 分工原则：根目录文档 = 叙事与总纲（不动）；本目录 = **细节知识**（可频繁更新、可回填实测数据）。

## 目录结构

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| （顶层）`prd.md` | 产品定义总纲：定位、痛点、核心闭环、验收口径、模块清单、技术选型、分步实现 | 已抽走的周排期（见 `plans/`）与简历话术（见 `reference/`） |
| `architecture/` | 系统长什么样：技术栈地图、Agent 主循环结构、迁移性设计 | 排期、决策过程 |
| `conventions/` | 怎么做事：编码纪律、提交与评审、安全护栏 | 为什么这么选型 |
| `design/` | 为什么做成这样：已定决策清单 + ADR（`design/adr/`，三条判据全中才写）+ 复杂功能设计文档（从 `design/feature-design-template.md` 复制） | 操作步骤 |
| `plans/` | 接下来做什么：M0–M9 路线图、周排期、M0 执行细案 | 已完成的沉淀知识 |
| `reference/` | 查字典：术语、数据集/工具资源、面试叙事 | 原创设计 |
| `reference/_sources/` | **原始母本（archived）**：外部调研与早期素材，仅供回溯出处 | 任何活跃内容；新调研直接写进上述目录 |
| `agents/` | Agent 协作的运行配置：issue 追踪器约定、triage 标签词汇 | 项目知识本身（放 `architecture/` 等） |

## 快速入口

- **想知道"系统怎么构成的"（权威）** → `architecture/architecture.md`（调研来源与取舍 + Harness 设计 + 数据模型 + 评测架构）
- **想知道"写码有什么规矩"（权威）** → `conventions/coding-standard.md`（工程约定 + Harness/工具/测试/安全红线）
- 快速查阅：技术栈速览 → `architecture/tech-stack-map.md`；主循环速览 → `architecture/agent-loop-design.md`
- 安全细节 → `conventions/security-guardrails.md`；编码纪律细节 → `conventions/coding-discipline.md`；提交评审 → `conventions/git-commit-review.md`；**约束层/质量门禁 → `conventions/quality-gates.md`**（配套：根目录 `pyproject.toml`、`.github/workflows/ci.yml`、`.pre-commit-config.yaml`、`tests/test_architecture_guards.py`）
- 想知道"这个选型为什么定了" → `design/decisions.md`
- 复杂功能开工前要讨论方案 → 复制 `design/feature-design-template.md` 新建 `<功能名>-design.md` 填写
- M8 展示层（Vue3 面板）设计 → `design/m8-showcase-design.md`（P0 六件核心信息件 + 演示故事线 + API 草案）
- M0 环境与混沌注入设计与任务分解 → `design/m0-environment-design.md`（reviewed；issue 在 `.scratch/m0-environment/`）
- M1 告警接入与归一化：设计 + 开发计划 → `design/m1-alert-ingestion-design.md`（draft，未进入 agent 阶段；issue 待建）；执行细案 → `plans/m1-execution.md`（seed）
- M4 证据链与过程存储：设计 + 开发计划 → `design/m4-evidence-chain-design.md`（reviewed，G1–G9 已定案 D-30–D-38；issue 在 `.scratch/m4-evidence-chain/`）
- M5 处置与恢复验证（四道闸门）：设计 + 开发计划 → `design/m5-remediation-gates-design.md`（reviewed，G1–G10 已定案 D-39–D-48；issue 在 `.scratch/m5-remediation-gates/`）
- 想知道"现在该干哪一步" → `plans/roadmap-m0-m9.md`
- 想查术语/数据集/面试话术 → `reference/`
- 想建 issue / 查 issue 状态 → `agents/issue-tracker.md`；triage 标签取值 → `agents/triage-labels.md`（issue 本体在根目录 `.scratch/`）

## 维护纪律

- 每份文档头部元信息必填：`title / summary / source / status / updated / read_when`
- `status` 三态：`seed`（骨架，待补）→ `active`（实战回填中）→ `stable`（已验证）；母本额外用 `archived`
- 实测数据回填，不得虚构；术语变更同步根目录 `CONTEXT.md`
- 防沉积：每几周审查一次，过期内容删掉或标记，不层层堆叠
- **母本外置**：跨项目的工程纪律母本不在本仓库，位于 `~/.workbuddy/开发规范与工作流程.md`；本仓库 `conventions/` 下三份 `active` 文件是唯一权威，改规范改这里。外部调研母本见 `reference/_sources/`（archived，只读）。
- **`.scratch/` 不忽略**：它是 issue 追踪器，**必须在版本控制内**才可追溯。若有人把它加回 `.gitignore`，等于让 issue 不可回溯——不要这么做。
