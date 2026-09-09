Status: needs-triage
Blocked by: 06

# 07 真实评测（key 门槛票 + 活栈环境门槛）

## 任务（附门槛，开工前需用户确认）

- **key 门槛票**：真实 LLM key（judge + 被评模型 profile）单独拍板落位（D-50 先例）
- **环境门槛**：活 demo 栈 + chaos 工具就绪；holdout 显式解锁（最终评测）
- 2 模型 × dev 集 N=3 + holdout 最终评测；实测数字回填报告与 README 挂图；成本超预算按 PRD 裁剪③（降单模型）上报

## 验收

- [ ] 实测矩阵报告产出（README 挂图），含 2 模型选型理由
- [ ] 全部数字实测回填、可回溯 eval_runs（禁虚构）
- [ ] 失败模式 100% 归类（unknown 人工通道有记录）

## Comments

- 设计引用：G2/G4/G8 验收口径 + R3（硬规 10）
