Status: needs-triage
Blocked by: 02, 04

# 05 N 遍矩阵 + 模型 profile 矩阵 + 报告双产物

## 任务

- N=3 跑法（均值±极差；同剧本 3 遍不一致标 `unstable` 单列）
- 模型 profile 参数化（每组 profile 一组 `ONCALL_LLM_*` env，**零硬编码模型名**；DeepSeek/Qwen 具体档位随 key 门槛票实测回填）
- `report.py`：JSON 明细 + 汇总 markdown（矩阵表 + 选型理由骨架）落 `datasets/eval/`（进 git，README 挂图引用）

## 验收

- [ ] mock 档 2 profile × 12 剧本 × 3 遍矩阵产出单测绿
- [ ] 报告每个数字可回溯 eval_runs 表行（禁虚构口径）抽验绿
- [ ] 全量 pytest 只增不减 + ruff 双检绿

## Comments

- 设计引用：G4/G5/G8
