---
title: "OnCall Copilot 架构文档"
summary: "Agent Harness 总体架构：单循环设计、上下文管理、工具系统、数据模型、评测与安全边界"
source: Anthropic 工程博客 × 5 + Claude Code 源码拆解 + SWE-agent ACI + OpenHands SDK + OpenDerisk 论文
status: active
updated: 2026-09-06
read_when: 设计/实现 M3–M7 任何模块前；架构决策存疑时
---

# OnCall Copilot 架构文档

## 1. 调研来源与取舍（为什么这样设计）

| 来源 | 核心结论 | 本项目取舍 |
|---|---|---|
| Anthropic《Building Effective Agents》 | 从最简模式起步，复杂度必须有可测量收益；**框架抽象是反模式**（遮蔽 prompt、难调试） | ✅ 采纳。自研轻量循环，不用 LangGraph 做主实现 |
| Anthropic《Effective Harnesses for Long-Running Agents》 | 长任务用**上下文重置 + JSON 结构化交接工件**优于原地压缩；模型会删失败测试、会"context 焦虑"提前收工 | ✅ 采纳。调查循环设计为可分段：证据链落库即交接工件，步数超限后新会话可续查 |
| Anthropic《Writing Tools for Agents》 | **工具设计是第一失败模式**；少而精 > 多而重叠；返回高信号数据；错误信息要指导下一步而非抛堆栈 | ✅ 采纳为工具开发铁律（见编码规范 §5） |
| Anthropic《Context Engineering》 | context rot 真实存在；上下文是有限资源；**just-in-time 加载**优于预载全部 | ✅ 采纳。证据摘要进上下文，原始输出落库 |
| Claude Code 源码拆解 | **单线程 Master Loop，扁平消息历史，不用 swarm**——选可调试性弃并行；权限三层审批；maxTurns 硬安全阀；60+ 工具收敛为 4 原语 + Bash | ✅ 采纳单循环 + 步数硬上限；⚠️ 部分改进：工具不走"原语收敛"，走"领域直查"（PromQL/LogQL 封装），因为运维取证需要确定性结果，模型编排反而绕路 |
| SWE-agent ACI 四原则 | 动作简单易懂 / 紧凑高效 / 反馈信息量足但简洁 / **护栏拦截错误传播**（编辑前 lint，坏了直接拒收） | ✅ 采纳。工具输出限长 + 摘要化 = 反馈紧凑；处置命令预校验 = 拒收机制 |
| OpenHands SDK V1 四原则 | **默认无状态，状态真值单一**（不可变组件 + 单一会话状态对象→确定性重放与恢复）；沙盒可选而非普遍 | ✅ 采纳。所有可变状态集中在 InvestigationSession 一个对象，组件构造时校验冻结 |
| Anthropic《Harness Design》 | **生成者与评判者必须分离**——模型自评一律偏宽松，独立调"怀疑型"evaluator 才有效 | ✅ 采纳为 Verifier 独立 prompt + 评测台 LLM-as-judge；⚠️ 不做独立评判 Agent 进主循环（步数/成本不允许），Verifier 以"证伪导向"prompt 实现轻量版 |
| OpenDerisk（蚂蚁） | 三层架构（感知/决策/报告）；专家多 Agent（SRE/Code/Report/Vis/Data）+ 可插拔推理引擎 + 知识引擎 + MCP 标准化；证据链可视化 | ⚠️ **明确取舍：不做 5-Agent 专家团队**。单 Agent + ≥6 工具已覆盖取证需求；多 Agent 的编排/调试/成本对学生项目是负资产。学习它的三层划分与证据链结构，面试讲清"为什么我不用多 Agent"本身就是深度 |
| Google SRE Book | SLO/错误预算/blameless postmortem | ✅ 概念层采纳，进告警规则与复盘报告结构 |

**一条总原则（来自 Claude Code 设计哲学，本项目照搬因为它是对的）**：模型决定做什么，Harness 决定允不允许、记不记录、超没超限——**推理与权限强制分离在不同代码路径上**。

## 2. 系统上下文

```
                    ┌──────────────────────────────────────────────┐
 告警源             │              OnCall Copilot 后端              │
 Prometheus ──┐     │  ┌─────────┐   ┌──────────────────────────┐  │
 Loki 规则  ──┼─Webhook─▶│ M1 接入 │──▶│ M2 降噪分类(规则+LLM)     │  │
              │     │  │ 归一化   │   │ 误报→归档 / 风险→观察     │  │
              │     │  └─────────┘   └──────────┬───────────────┘  │
              │     │                     真实事件                 │
 demo 业务系统 │     │  ┌──────────────────────────────────────┐   │
 (compose) ───┼─────┼─▶│      M3 Agent Harness（单循环）        │   │
  ↑ Pumba 注入 │     │  │ Loop + Tools + Context + Verifier    │   │
              │     │  └───────┬───────────────────┬──────────┘   │
 P/L/G 栈 ◄───┼─────┼──取──────┘        处置确认──┤              │
 (取证数据源)  │     │ 证                          ▼              │
              │     │  ┌──────────┐    ┌──────────────────────┐   │
 人 ◄─────────┼─────┼──┤ M4 证据链 │    │ M5 处置(四道闸门)     │   │
 (确认门/UI)  │     │  │ 落库/导出 │    │ 干跑→确认→执行→验证   │   │
              │     │  └──────────┘    └──────────────────────┘   │
              │     │  ┌──────────┐    ┌──────────────────────┐   │
              │     │  │ M6 知识库 │    │ M7 评测台             │   │
              │     │  │ RAG 召回  │    │ 黄金集/矩阵/回归       │   │
              │     │  └──────────┘    └──────────────────────┘   │
              │     └──────────────────────────────────────────────┘
```

## 3. Agent Harness 核心设计（M3，系统的心脏）

### 3.1 单循环骨架

```python
# 伪代码——真实实现约束见编码规范
def investigation_loop(session: InvestigationSession) -> InvestigationResult:
    while not session.terminated:
        step = planner.decide(session.context_window)      # LLM: {thought, action} | {conclusion}
        result = tool_registry.execute(step.action)        # Harness: 执行+截断+落库
        session.record(step, result)                       # 证据链 100% 落库（M4 接口）
        session.context_window.append(summarize(result))   # 只进摘要，原始输出在库
        session.hypotheses.update(verifier.check(session)) # 证伪导向：证实/推翻
        if session.step_count >= MAX_STEPS:                # 硬安全阀 = 15
            session.escalate_to_human()
    return session.result()
```

### 3.2 六个组件的职责边界

| 组件 | 职责 | 明确不做 |
|---|---|---|
| **Loop** | 推进 step、终止判定、异常兜底 | 不做任何业务判断 |
| **Planner** | 每步产出 `{thought, action}` 或 `{conclusion}`，结构化输出 + Pydantic 校验 | 不直接执行 |
| **ToolRegistry** | 工具注册、参数校验、执行、超时、重试、输出截断、指标埋点 | 不改写语义 |
| **ContextManager** | 上下文预算（工具输出摘要化、系统提示瘦身、过期证据降权） | 不丢弃原始数据（落库不落窗） |
| **Verifier** | 证伪导向检查：证据是否支持当前假设；每次工具结果后触发 | 不生成新计划 |
| **PermissionGate** | 工具分级（只读/写操作）；写操作强制走四道闸门 | 不可被 prompt 绕过（独立代码路径） |

### 3.3 上下文管理策略（对标 Anthropic，量化）

- 工具输出上限：单次 ≤ 2000 tokens 进上下文，超出部分截断 + 完整输出落库，上下文里放 `[truncated, full at step N]` 指针
- 系统提示 ≤ 1500 tokens：角色 + 工具一览（名称/一句话/何时用）+ 输出协议；**工具详情 just-in-time**——模型可调 `tool_help` 查详情，不预载
- 证据摘要模板固定（组件/指标/异常方向/时间窗），压缩 KV cache 重复前缀
- 步数 ≥ 10 时 ContextManager 主动把已证伪假设移出主上下文（落库保留）

### 3.4 防失控三闸

1. `MAX_STEPS = 15`（硬编码于 Harness，不在 prompt 里）
2. 单工具执行超时 30s、重试 ≤ 2 次、失败归类 `tool_error`
3. 权限三层（照搬 Claude Code）：L0 只读自动放行 / L1 写操作需 API 确认 / L2 高危（五级操作分级第 5 级）永远禁止

### 3.5 与多 Agent 的关系（面试必问）

v1 单循环；预留两个演进点但不实现：① evaluator 分离（M7 评测台里的 LLM-as-judge 已是轻量版）；② 并行取证（只读工具可 parallel tool call，写操作永不并行）。

## 4. 数据模型（SQLite 起步，六张核心表）

```
scenarios      剧本: id, name, fault_type, inject_script, expected_root_cause, expected_action
alert_events   告警: id, fingerprint, source, labels_json, fired_at, status(deduped/classified)
                     + M1 评审增列（2026-09-06，见 design/m1-alert-ingestion-design.md G5 与 decisions.md D-13）:
                     dedup_count, last_fired_at, resolved_at, annotations_json(原始 payload + AM fingerprint 溯源)
incidents      事件: id, alert_ids[], severity, status(investigating/mitigated/closed), created_at
evidence_steps 证据: id, incident_id, step_no, thought, tool, input_json, output_json,
                     output_summary, tokens, cost, latency_ms, ts        ← 100% 落库
hypotheses     假设: id, incident_id, text, status(confirmed/rejected/active),
                     supporting_steps[], against_steps[]
eval_runs      评测: id, scenario_id, model, hit_top1, hit_top3, steps, duration_s,
                     cost_cny, failure_mode, run_at
```

设计约束：`evidence_steps.output_json` 存原始输出（可回溯），`output_summary` 存进上下文的摘要——**两个都要有**（Anthropic："不能只存摘要"）。组件不可变、构造时校验；`InvestigationSession` 是唯一可变状态对象（OpenHands 原则），序列化即断点恢复工件。

## 5. 关键流程时序（正常路径）

```
告警 webhook → 指纹去重(M1) → 规则通道判定 → 不确定 → LLM 通道分类(M2)
  → 真实事件建档 → Harness 启动调查(M3)
  → [查指标 → 摘要入窗 → Verifier → 查日志 → ... 假设证实] ≤15 步
  → 根因报告 + 证据链(M4) → 匹配 SOP → 干跑 → 人工确认(M5)
  → 执行 → 回查指标 → 恢复? → 闭环报告 → 向量化入库(M6)
评测台(M7) 独立入口：scenario → 注入 → 等 alert → 全自动跑 → 指标矩阵
```

## 6. 评测架构（M7）

- **双集隔离**：`scenarios/dev/*`（可调 prompt）vs `scenarios/holdout/*`（终评专用，调参禁看）
- 判对错两级：规则匹配（组件+故障类型）→ LLM-as-judge（rubric 五维：根因正确/证据充分/排除合理/成本步数/幻觉有无）+ 人工抽检 20%
- 失败模式强制归类：`tool_error / plan_error / timeout / hallucination / no_signal / premature_stop`
- 评测结果导向过程分析而非只看命中率（Anthropic：读 trace 找失败模式，比刷分有用）
- 每次 commit 跑回归（dev 集），README 挂矩阵

## 7. 安全边界（详见 conventions/security-guardrails.md）

只读工具默认全放行；写操作四道闸门（干跑→确认门→受控执行→恢复验证）；白名单而非黑名单；确认门在 API 层不在 prompt 层；送模型前脱敏；每步审计落库。

## 8. 明确不做（防蔓延，也是面试谈资）

多 Agent 专家团队 / LangGraph 主实现 / K8s 与 Chaos Mesh 实际部署（只兼容叙事）/ 长期记忆系统 / 微服务化拆分（单体 FastAPI 足够）/ 流式 UI 优先于评测台。
