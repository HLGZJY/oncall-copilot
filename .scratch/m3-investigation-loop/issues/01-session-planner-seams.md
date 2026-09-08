Status: ready-for-agent
Blocked by:

# 01 调查会话契约与 Planner 接缝（T1 / G1·G4 推荐）

## 任务

M3 调查循环的领域契约与 LLM 决策接缝，落 `src/oncall/harness/`（新包）：

- **调查会话契约（G4）**：`InvestigationSession`（唯一可变状态：incident 锚点 / 步计数 / 状态 running|concluded|escalated|aborted / 终止原因）、`EvidenceStep`（step_no/thought/tool/input_json/output_json/output_summary/tokens/cost_cny/latency_ms/ts——字段对齐架构 §4 `evidence_steps` 冻结列，`output_json` 与 `output_summary` 两个都要）、`Hypothesis`（text/status: confirmed|rejected|active/supporting_steps/against_steps——对齐架构 §4 `hypotheses`）；**内存契约不建表**，Pydantic 序列化即断点恢复工件
- **Planner 接缝（G1）**：`PlannerClient` Protocol（`decide(context_view) -> PlannerDecision`）+ `PlannerDecision`（`{thought, next_tool, args}` 或 `{conclusion}` 二选一，Pydantic 校验）+ 异常族 `PlannerOutputError` / `PlannerTimeoutError`（语义逐字对齐 M2 `oncall.classify.client` 的 `LLMOutputError`/`LLMTimeoutError`——**harness 禁 import classify（C3），异常在 harness 自持、文档互引**）
- **MockPlanner**：可编程 script 回放（PlannerDecision 与异常实例混排、按序消费、耗尽稳定回落默认结论），照 M2 `MockLLMClassifier` 先例

## 要点

- 契约 frozen 数据结构 + session 可变容器分离（OpenHands 原则：组件不可变、状态单一）
- `PlannerDecision` 两分支互斥：给了 `next_tool` 就无 `conclusion`，反之亦然——校验要在模型层钉死
- TDD 红绿先行；时间字段注意 pydantic `mode="json"` UTC 序列化为 `Z` 后缀（踩坑 ⑥）
- C6 单文件 ≤300 行、A2 禁内联超长 prompt（本票无 prompt）；命名一律 CONTEXT.md 词汇，新术语待 G 定案后统一入表

## 验收（可机械判定）

- [ ] `EvidenceStep`/`Hypothesis` 序列化 roundtrip 单测绿（JSON 与 dict 双向；字段集合与架构 §4 冻结列精确匹配）
- [ ] `InvestigationSession` 状态机单测绿（步计数推进 / 终止四态 / 序列化恢复 roundtrip）
- [ ] `PlannerDecision` 校验单测绿（两分支合法样例通过 / 互斥违反与缺字段拒绝 / args 非 dict 拒绝）
- [ ] MockPlanner 契约单测绿（正常/畸形/超时三夹具按序回放、异常实例透传、耗尽回落默认、调用入参记录可断言）
- [ ] 全量 pytest + ruff check + ruff format --check 绿（基线 280 passed / 4 skipped / 97.12% 不回退）
