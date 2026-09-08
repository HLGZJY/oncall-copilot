---
title: "Agent 主循环设计"
summary: "自研 ReAct 循环的结构、防伪 Agent 判据、防绕圈五机制与证据链数据形状"
source: docs/reference/_sources/项目信息.md §3.3 模块C + §6.4 + docs/prd.md §5/§7（模块 M3/M4）
status: seed
updated: 2026-09-06
read_when: 实现 M3（自主调查循环）与 M4（证据链）时
---

# Agent 主循环设计

## 结构图

```
告警/问题 ─► Planner（下一步做什么） ──► Tool Registry（≥6 个工具）
             ▲                              │
             │                              ▼
        记忆（本轮证据摘要）◄──────── 执行工具（查指标/查日志/查变更/异常检测/查知识库/处置）
             │
             ▼
        Verifier（证据支持假设？）─ 否 → 换假设 / 步数超限(15) → 求助人类
             │ 是
             ▼
        结论 + 证据链报告
```

## 工具清单（≥6）

① query_metrics（PromQL 封装）② search_logs（Loki 封装）③ detect_anomaly（统计基线 + IsolationForest 双通道）④ query_kb（RAG）⑤ get_topology（变更/拓扑元数据）⑥ execute_action（处置，M5 前先 stub）

## 每轮协议

- system prompt（角色/工具说明/输出协议）→ 取证据摘要 → 模型出 `{thought, next_tool, args}` 或 `{conclusion}`
- 结构化输出解析容错；工具异常重试 2 次；单轮超时保护
- **分层调用降成本**（OpenDerisk 实践）：推理模型做 plan，便宜模型做简单取证

## 防伪 Agent 三判据（面试第一防线）

1. 任务步骤、跳转、终止条件全部由模型在每个 step 依据证据动态决定
2. 固定 SOP 只是 Agent 的工具之一，调用时机由模型判断
3. 自带自校验（Verifier）+ 回退重试 + 步数上限

## 证据链数据形状（M4，可直接用）

```json
{
  "incident_id": "...",
  "steps": [{"step_no": 1, "thought": "...", "tool": "query_metrics",
             "input_json": {}, "output_summary": "...", "ts": "...", "tokens": 0, "cost_cny": 0.0}],
  "hypotheses": [{"text": "...", "status": "confirmed|rejected",
                  "supporting_steps": [2,5], "against_steps": [4]}],
  "conclusion": "...", "confidence": 0.82
}
```

三必须：每步可回溯原始工具输出；假设带 confirmed/rejected 状态；报告导出 JSON/Markdown。
