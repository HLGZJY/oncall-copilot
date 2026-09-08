Status: resolved
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

- [x] 验收节每条均有对应测试且全绿（逐条映射清单落本票 Comments）
- [x] `tests/test_architecture_guards.py` 全绿（C3/C4/C5/C6/A2）
- [x] 全量 pytest：479 passed / 7 skipped 基线只增不减；coverage ≥80%
- [x] ruff check + ruff format --check 全绿

## Comments

### 2026-09-08 T7 落位：验收节 9 条 × 测试映射清单（T7 收口）

> 基线口径注记：本票面原文 479/7/98.05% 是 04 时代旧基线；06 落位后实测为
> **537 passed / 7 skipped / coverage 98%**，T7 实测后为 **541 passed / 7 skipped /
> 98%（pyproject 门禁线 ≥80%）**——只增不减成立，设计文档历史行文不回改。

| # | 验收条目 | 对应测试 | 新增/已有 | 状态 |
|---|---|---|---|---|
| 1 | 一次完整调查 100% 落库（3 剧本经 POST /investigate，三表行数/终态） | `tests/e2e/test_m4_acceptance_persistence.py::test_three_scenarios_full_persistence_after_post[cpu-spike/slow-sql/queue-backlog]`（行数=步数/假设池、investigations 恰一行且终态一致）；repo 层 `test_db_evidence_repo.py::test_three_step_concluded_investigation_fully_persisted` | **新增** + 已有 | ✅ |
| 2 | 任意一步可回溯原始工具输出（output_json ↔ ToolResult status/data/meta 逐字段；escalated/aborted 可查 D-28） | 同上文件 `test_output_json_matches_known_tool_result_field_by_field`（已知 ToolResult 精确对账）+ `test_three_scenarios_full_persistence_after_post`（DB 行 ↔ 响应步逐字段）；D-28：`test_db_evidence_repo.py::test_escalated_via_loop_persists_evidence_gathered` / `test_aborted_finalize_keeps_gathered_evidence` + `test_investigation_api.py::test_escalated_report_queryable_via_get` | **新增** ×2 + 已有 | ✅ |
| 3 | 导出 JSON 与 agent-loop-design 数据形状比对一致（D-35 冻结键集合） | `test_db_views_report.py::test_key_sets_match_frozen_contract`（== 精确守卫）+ `test_investigation_api.py::test_roundtrip_matches_in_memory_export_field_by_field` | 已有（03 落位，核对） | ✅ |
| 4 | Markdown 导出可用（200 + text/markdown + 全部步/假设/结论/终态） | `test_investigation_api.py::test_markdown_export_returns_200_text_markdown_with_full_content` / `test_markdown_content_matches_json_report_data` / `test_escalated_report_exportable_via_markdown` / 404 语义 ×2 | 已有（04 落位，核对） | ✅ |
| 5 | M3/M4 边界不破（三表契约字段 + 指针接口不变，D-25） | `test_db_evidence_models.py` 列集合精确断言 ×4（D-31/§4 逐字段）+ `test_db_evidence_repo.py::test_row_id_backfill_keeps_memory_contract_untouched` | 已有（01/02 落位，核对） | ✅ |
| 6 | 缺口① opening 视图（D-37 键集合 + C6 全绿） | `test_decision_view.py::test_view_key_set_exact_with_opening_none_default` / `test_projection_key_set_exact_d37` + `test_architecture_guards.py` C6 | 已有（05 落位，核对） | ✅ |
| 7 | 缺口② schema 可见性（六工具 schema 摘要 + ≤1500 tokens） | `test_harness_context_manager.py::test_schema_summary_within_1500_after_d38` / `test_schema_aligns_with_registry_contracts` / `test_lists_all_six_tools_from_specs` / `test_within_1500_tokens` | 已有（06 落位，核对） | ✅ |
| 8 | 重跑真实实测（步数/耗时/成本/Top-1 回填） | **归 T8**（issue 08 范围，需用户确认 LLM key 后开工） | — | ⏸ 归 T8 |
| 9 | 全量门禁只增不减（pytest + ruff 双检 + coverage ≥80%） | 本次实测：**541 passed / 7 skipped / coverage 98%**（基线 537 只增不减）+ ruff check / ruff format --check 全绿（177 文件） | 本次核对 | ✅ |

新增测试：**4 条**（`tests/e2e/test_m4_acceptance_persistence.py`，生产代码零改动）。
架构守卫：`test_architecture_guards.py` 7 passed（C3/C4/C5/C6/A2 全绿）。
08 就绪确认：08 被 05/06/07 阻塞，本票 resolved 后已解除阻塞；**开工前需用户确认 LLM API key**（T8 真实调用门槛）。
