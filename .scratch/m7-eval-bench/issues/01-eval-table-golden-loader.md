Status: ready-for-agent

# 01 eval_runs 第十表 + eval 模块骨架 + golden 加载器（防泄漏守卫）

## 任务

- 新增第十表 `eval_runs`（字段见设计文档「数据模型变更」），随票回写架构 §4（D-31/D-46/D-55 显式偏差通道先例）
- `src/oncall/eval/` 模块骨架（C6 ≤300 行/文件）
- `golden.py`：dev/holdout 双集加载器；**防泄漏守卫**：① holdout 未显式解锁（env 开关）时加载即 fail ② few-shot 源含 3 验证剧本即 fail ③ 加载结果带标注完整性校验（缺 root_cause/期望处置即 fail）

## 验收（可机械判定）

- [ ] 防泄漏三条守卫各有单测绿
- [ ] eval_runs 表 frozen-face 契约测试绿（D-25 先例）
- [ ] 全量 pytest 只增不减 + ruff 双检绿

## Comments

- 设计引用：`docs/design/m7-eval-bench-design.md` G1/G5 + R6（M2 issue 03/05 防泄漏先例）
