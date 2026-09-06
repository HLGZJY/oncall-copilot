---
title: "Triage Labels"
summary: "五个标准 triage 角色的映射表：needs-triage / needs-info / ready-for-agent / ready-for-human / wontfix"
source: mattpocock/skills → skills/engineering/setup-matt-pocock-skills/triage-labels.md
status: active
updated: 2026-09-06
read_when: 给 issue 打状态标签时；判断某 issue 能否直接交给 Agent 做时
---

# Triage Labels

Agent 技能用五个标准 triage 角色说话。本文件把这些角色映射到本仓库 tracker 里实际使用的字符串——因为是本地 markdown tracker，二者同名。

| 标准角色 | 本仓库使用 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 待评估：维护者还没看过 |
| `needs-info` | `needs-info` | 等信息：问题本身不完整，无法判断 |
| `ready-for-agent` | `ready-for-agent` | **已充分定义，可直接交给 Agent 离线执行** |
| `ready-for-human` | `ready-for-human` | 需要人来动手（涉及判断、取舍、或太模糊） |
| `wontfix` | `wontfix` | 不做 |

当某个 skill 提到某个角色（例如"打上 AFK-ready 标签"），用上表右列的字符串。

## 本项目的判据

- **`ready-for-agent` 是硬门槛**：一个 issue 只有在"验收标准可机械判定"时才配这个标签。模糊需求一律留在 `needs-triage`，不要让 Agent 猜。
- **`ready-for-human` 不等于低优先**：涉及架构取舍（如换向量库、改主循环结构）的都归这里——控制权在人。
- **`wontfix` 要写原因**：避免同一个想法被反复提出。

## 若日后改用 GitHub Issues

只需把右列改成 GitHub 上的真实标签名，其余约定（见 [`issue-tracker.md`](issue-tracker.md)）不变。
