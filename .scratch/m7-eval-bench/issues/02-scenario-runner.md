Status: needs-triage
Blocked by: 01

# 02 剧本 runner（LoopComponents 装配 + mock/真实双档）

## 任务

- `runner.py`：对每个剧本装配既有 LoopComponents 跑 `run_investigation`，产出 `EvalRun` 明细（消费 `InvestigationResult` 既有列，不改 harness 契约面）
- 双档：mock 档（MockPlanner/确定性组件，CI 可跑）+ 真实档（`ONCALL_RUN_M7_EVAL=1` + 真实 planner，先例 `tests/integration/test_m5_real_e2e.py`）
- 不加工具、不扩 D-23 冻结面

## 验收

- [ ] mock 档单剧本 3 遍跑通、明细落 eval_runs 单测绿
- [ ] 真实档默认跳过（env 未设 skip 单测绿，照 M5 issue 08 惯例）
- [ ] 全量 pytest 只增不减 + ruff 双检绿

## Comments

- 设计引用：G1/R4（loop.py 装配面）
