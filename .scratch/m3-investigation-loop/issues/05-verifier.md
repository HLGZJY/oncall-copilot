Status: resolved
Blocked by: 03, 04

# 05 Verifier：规则层 + LLM 裁决接缝（T5 / G5 定案 = D-26）

> **就绪态终审**：G5 已于 2026-09-08 定案（用户采纳推荐解，登记 D-26），本票转 `ready-for-agent`；票面即定案口径。

## 任务（D-26 定案口径）

- **规则层**（每步零成本确定性校验）：ToolResult 状态检查 / args 与工具 schema 合法性 / 假设-证据步引用存在性（supporting_steps/against_steps 指向的 step_no 必须存在）/ **hallucination 判定**——结论或假设引用的工具输出在 session 中不存在即检出
- **LLM 裁决接缝**：两个触发时机——新假设提出时、收束判定时，**每次调查 ≤3 次**；`VerifierJudge` Protocol + Mock 实现（照 MockPlanner 夹具风格）；证伪导向独立 prompt（生成者与评判者分离，架构 §1）；裁决输出 `{supported: bool, reason}`，不生成新计划（架构 §3.2）
- 裁决结果驱动假设状态流转：confirmed / rejected（写回 session.hypotheses）

## 要点

- 规则层是纯函数族（session + decision → findings），TDD 接缝；LLM 裁决计数器在 session 上，超限即拒绝调用并降级为规则层结论
- prompt 模板落模块级常量或模板文件（A2）
- Mock 夹具需支持：支持/推翻/超时三态，供 T6 编排测试

## 验收（可机械判定）

- [x] 规则层各校验正反例单测绿（含 hallucination 判定：引用不存在的 step_no 被检出）
- [x] LLM 裁决次数上限单测绿（第 4 次调用被拒、降级路径成立）
- [x] 裁决结果驱动假设流转单测绿（confirmed/rejected 写回 session，supporting/against 步引用合法）
- [x] Verifier 零计划产出单测绿（返回结构中无 next_tool/计划字段）
- [x] 全量 pytest + ruff 双检绿

## 落地注记（2026-09-08）

- **落位**：`src/oncall/harness/verifier.py`（300 行，C6 压线达标）+ `tests/unit/test_harness_verifier.py`（29 测试）
- **计数器归属偏差**：票面原文「计数器在 session 上」，但 `session.py` 为冻结文件不可加字段——按派工 prompt §3 预授权边界，实现为 Verifier 实例内按 `id(session)` 键控的独立计数状态（C8 合规：不落模块级 dict）；模块 docstring 与派工 prompt 均已注记
- **门禁实测**：423 passed / 4 skipped（基线 394 只增不减）· coverage 97.53%（C9 门槛 80%）· `ruff check` + `ruff format --check` 全绿
- **裁决契约**：`VerifierVerdict{supported, reason}` extra=forbid——`next_tool` 等计划字段在契约层即被拒（零计划产出的模型层保证）；异常族 `VerifierOutputError`/`VerifierTimeoutError` 自持，语义逐字对齐 planner.py 先例（C3）
- **降级路径**：超限 / 未注入 judge / 畸形 / 超时四种情形统一降级为规则层结论，返回 `JudgmentOutcome(verdict=None, degraded=True)`，不抛错中断调查（T6 主循环直接消费）
- **Mock 夹具**：`MockVerifierJudge` 支持/推翻/超时三态混排回放 + 耗尽回落（缺省不支持——证伪导向缺省立场），T6 编排测试可直用
