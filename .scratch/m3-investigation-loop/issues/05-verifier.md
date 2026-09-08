Status: ready-for-human
Blocked by: 03, 04

# 05 Verifier：规则层 + LLM 裁决接缝（T5 / G5 待定案）

> **ready-for-human 原因**：Verifier 形态（纯规则 / LLM 每步 / 混合）是设计文档 G5 开放点，属架构取舍，待用户评审拍板后方可派工。本票票面按**推荐解（规则+LLM 混合）**预写，定案被推翻时本票重写。

## 任务（按 G5 推荐解）

- **规则层**（每步零成本确定性校验）：ToolResult 状态检查 / args 与工具 schema 合法性 / 假设-证据步引用存在性（supporting_steps/against_steps 指向的 step_no 必须存在）/ **hallucination 判定**——结论或假设引用的工具输出在 session 中不存在即检出
- **LLM 裁决接缝**：两个触发时机——新假设提出时、收束判定时，**每次调查 ≤3 次**；`VerifierJudge` Protocol + Mock 实现（照 MockPlanner 夹具风格）；证伪导向独立 prompt（生成者与评判者分离，架构 §1）；裁决输出 `{supported: bool, reason}`，不生成新计划（架构 §3.2）
- 裁决结果驱动假设状态流转：confirmed / rejected（写回 session.hypotheses）

## 要点

- 规则层是纯函数族（session + decision → findings），TDD 接缝；LLM 裁决计数器在 session 上，超限即拒绝调用并降级为规则层结论
- prompt 模板落模块级常量或模板文件（A2）
- Mock 夹具需支持：支持/推翻/超时三态，供 T6 编排测试

## 验收（可机械判定）

- [ ] 规则层各校验正反例单测绿（含 hallucination 判定：引用不存在的 step_no 被检出）
- [ ] LLM 裁决次数上限单测绿（第 4 次调用被拒、降级路径成立）
- [ ] 裁决结果驱动假设流转单测绿（confirmed/rejected 写回 session，supporting/against 步引用合法）
- [ ] Verifier 零计划产出单测绿（返回结构中无 next_tool/计划字段）
- [ ] 全量 pytest + ruff 双检绿
