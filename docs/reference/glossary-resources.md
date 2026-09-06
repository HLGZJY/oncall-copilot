---
title: "术语与数据资源速查"
summary: "AIOps 关键术语索引 + 数据集/开源环境/混沌工具清单"
source: docs/reference/_sources/项目信息.md 第1部分 + 附录B + docs/reference/_sources/数据集介绍.md
status: seed
updated: 2026-09-06
read_when: 术语拿不准、找数据集/工具时
---

# 术语与数据资源速查

## 术语（权威定义见根目录 `CONTEXT.md`；详表见 `_sources/项目信息.md` 第 1 部分，⭐ 为必掌握）

| 层 | 核心 |
|---|---|
| 运维 | ⭐SLA/SLO/SLI、⭐MTTR、错误预算、P0–P3、⭐On-call、Runbook/SOP、变更（80% 故障来自变更） |
| 可观测 | 三支柱、⭐OTel、⭐Prometheus/PromQL、Loki、⭐RED/USE、Trace/Span、高基数陷阱 |
| 数据算法 | ⭐降噪/收敛、⭐告警指纹、告警风暴、⭐RCA、Top-1/Top-3、⭐时序异常检测、动态基线 |
| AI | ⭐Tool Calling、⭐ReAct、Planner/Verifier、⭐MCP、Multi-Agent、⭐RAG、幻觉、⭐证据链 |
| 工程治理 | ⭐混沌工程、⭐故障剧本、⭐黄金集、⭐人工确认门、干跑、白名单 vs 黑名单、脱敏、AgentOps |

> 项目内权威定义以根目录 `CONTEXT.md` 为准，本表只做通用术语索引与资源导航，不重复定义。

## 数据集

| 名称 | 用途 |
|---|---|
| OpenRCA（微软，~20GB） | RCA 黄金基准 |
| SNAP 微服务故障集 | 10+ 类标注故障，Top-1/Top-3 评测背书 |
| LogHub | 日志检索/异常识别验证 |
| Google Cluster Trace | 资源类故障算法验证 |

## 开源环境与工具

| 名称 | 用途 |
|---|---|
| 极简自研 compose（主环境） | 日常开发调试 |
| OTel Astronomy Shop | 迁移演示（上云，非关键路径） |
| Sock Shop | 级联故障/告警风暴备选 |
| Pumba（首推）/ Chaos Mesh | Docker/云原生混沌注入 |
| Locust / k6 | 流量型故障 |
| OpenDerisk（蚂蚁，必读） | 多智能体 RCA 标杆，学设计不改代码 |

## 学习资料优先级

_sources/项目信息.md → OpenDerisk 论文+仓库 → Prometheus/PromQL 官方文档 → MCP 规范 → Google SRE Book
