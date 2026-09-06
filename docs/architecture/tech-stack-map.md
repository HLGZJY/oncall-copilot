---
title: "技术栈地图"
summary: "L0–L5 自下而上的分层选型与每层的面试叙事点"
source: docs/reference/_sources/项目信息.md §3.4 + docs/prd.md §6（技术选型）
status: seed
updated: 2026-09-06
read_when: 新增依赖、调整选型、或被面试官问"为什么用 X"时
---

# 技术栈地图

```
L5 交互层     Vue3 轻量面板（时间线/证据链/报告）+ SSE
L4 Agent 层   自研 ReAct 循环：Planner + Tool Registry(≥6) + 记忆 + Verifier
              LLM: DeepSeek / Qwen-plus（统一 OpenAI-compatible，可热切换）
              知识层: Chroma(起步) → pgvector；Embedding: BGE-M3
L3 可观测层   Prometheus(+prometheus_client) / Loki / Grafana；OTel SDK 可选
L1 数据执行层 SQLite→MySQL / Redis / Pumba+自定义脚本+Locust / Docker Compose
L0 工程底座   Python 3.11+ / FastAPI(async+SSE) / pytest / GitHub Actions
```

## 关键选型理由（一句话版）

| 选型 | 理由 / 叙事点 |
|---|---|
| 自研循环，不 fork 框架 | "框架原理我写过"；可控、可插桩评测；附 LangGraph 对比版 |
| LLM 用国产便宜模型 | 成本叙事 + 评测时 ≥2 模型对比 |
| Chroma → pgvector | 起步轻，演进有理由即可 |
| SQLite → MySQL | 复用既有经验；事件/证据/报告/评测结果 |
| Compose 不上 K8s | 学生场景零门槛；Chaos Mesh 只在面试提一句"兼容" |

## 迁移性设计（可迁移 ≠ 通用）

- Agent 核心 = 深模块，系统无关；环境通过 **adapter（接缝）** 接入
- 换系统 = 只换适配器配置（Prometheus 地址/标签），Agent 核心一行不动
- 证明方式：同一套代码跑通两个环境（自研 compose + 第二环境）
- 口径克制："架构对任何 Prometheus 系统可迁移"，不说"对任何系统通用"
