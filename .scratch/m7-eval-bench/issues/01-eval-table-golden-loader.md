Status: resolved

# 01 eval_runs 第十表 + eval 模块骨架 + golden 加载器（防泄漏守卫）

## 任务

- 新增第十表 `eval_runs`（字段见设计文档「数据模型变更」），随票回写架构 §4（D-31/D-46/D-55 显式偏差通道先例）
- `src/oncall/eval/` 模块骨架（C6 ≤300 行/文件）
- `golden.py`：dev/holdout 双集加载器；**防泄漏守卫**：① holdout 未显式解锁（env 开关）时加载即 fail ② few-shot 源含 3 验证剧本即 fail ③ 加载结果带标注完整性校验（缺 root_cause/期望处置即 fail）

## 验收（可机械判定）

- [x] 防泄漏三条守卫各有单测绿——`test_holdout_locked_by_default` / `test_holdout_unlocked_with_env`（守卫① `ONCALL_M7_HOLDOUT_UNLOCK=1`）；`test_fewshot_filter_excludes_validation_slugs` / `test_fewshot_assert_raises_on_leak`（守卫②复用 `classify.llm.fewshot.VALIDATION_SCENARIO_SLUGS` 冻结名单）；`test_missing_root_cause_rejected` / `test_missing_scenario_rejected` / `test_duplicate_scenario_rejected`（守卫③）+ 真实 dev 12 剧本加载 `test_load_real_dev_set`
- [x] eval_runs 表 frozen-face 契约测试绿（D-25 先例）——`test_tenth_table_registered_and_frozen_columns`（列集合精确钉死 17 列）+ 四条 CHECK 约束（the_set/verdict/judged_by/failure_mode 七值）+ `test_eval_run_not_linked_to_investigations`（无 incident 外键，分表不混口径）+ 既有九表列集合回归
- [x] 全量 pytest 只增不减 + ruff 双检绿——实测 **727 passed / 13 skipped**（基线只增不减），`ruff check` + `ruff format --check` 0 错误；bandit 0 finding；import-linter 2.15+grimp 3.x 已知环境漂移（M5 issue 08 在案），C3/C4/C5 由 AST 守卫同等覆盖全绿

## Comments

- 设计引用：`docs/design/m7-eval-bench-design.md` G1/G5 + R6（M2 issue 03/05 防泄漏先例）；D-58–D-65 已登记
- 2026-09-09（T1 执行）：第十表落 `src/oncall/db/eval_models.py`（独立文件——models.py 追加后 360 行触发 C6 ≤300 门禁，拆出后 models.py 恰 300 行；共用同一 Base 元数据，import 即注册进 create_all，`oncall.db.__init__` 导出 EvalRun）。`oncall/eval/` 骨架：`__init__.py` + `golden.py`（pydantic `GoldenScenario` 契约模型；`load_golden_dir` 三守卫全开；`filter_fewshot_scenarios` 返回 (保留, 排除) 二元组供 few-shot 组装消费保留侧）。架构 §4 已回写第十表。runner/judging/metrics/report 四文件按 T2–T5 逐步落位。诊断记录：表清单钉死类测试（evidence_models/remediation_proposals/alert_events）随第十表演进更新（前沿断言惯例，M2 先例）；`eval_runs` 字母序位于 `alert_events` 与 `evidence_steps` 之间。零真实 LLM 调用、零容器操作。
