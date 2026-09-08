Status: ready-for-agent
Blocked by: 01, 02, 03, 04, 05, 06

# 07 验收断言与门禁（T7）

## 任务

- 设计文档「验收标准」节逐条转**机械断言**（本票是断言汇总票，各票已带的单测在此收口核对）：
  - 100% 落库：mock 3 剧本 conclusion 后 `evidence_steps` 行数 = 会话步数、`hypotheses` 行数 = 假设池大小、`investigations` 一行且终态一致
  - 任意一步回溯：任一 `evidence_steps.output_json` 与该步 `ToolResult`（status/data/meta）逐字段一致；escalated/aborted 已取证部分可查
  - 导出 JSON 与 agent-loop-design 数据形状比对一致（按 D-35 定案口径：冻结契约键集合）
  - M3/M4 边界不破：`EvidenceStep`/`Hypothesis`/`InvestigationSession` 契约字段与 M3 一致（import + 键集合断言），指针接口不变
- 架构守卫全绿核对：C3（harness 零 import 禁列）/ C4/C5 / C6（全仓 ≤300 行）/ A2 / import-linter
- 全量门禁基线核对：**479 passed / 7 skipped / 98.05%** 只增不减 + ruff check + ruff format --check

## 要点

- 断言缺失项在本票补齐；断言与设计文档验收节**逐条对应**（测试名可映射到验收条目）
- 发现验收条目无法机械判定时停手上报（`ready-for-human` 复审），不许放宽断言凑绿

## 验收（可机械判定）

- [ ] 验收节每条均有对应测试且全绿（逐条映射清单落本票 Comments）
- [ ] `tests/test_architecture_guards.py` 全绿（C3/C4/C5/C6/A2）
- [ ] 全量 pytest：479 passed / 7 skipped 基线只增不减；coverage ≥80%
- [ ] ruff check + ruff format --check 全绿
