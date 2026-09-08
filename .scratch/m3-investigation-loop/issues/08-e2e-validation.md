Status: ready-for-agent
Blocked by: 07

# 08 端到端 3 剧本验证与收尾（T8 / G9；真实调用前需用户确认 key）

## 任务

- **mock 端到端**：`cpu-spike` / `slow-sql` / `queue-backlog`（G9，PRD §7-M3 点名三类）经 `POST /investigate` 用 MockPlanner 脚本驱动走至 conclusion；结论根因与 golden `root_cause` **规则匹配级**比对（关键实体+动作词；LLM-as-judge 归 M7）
- **真实 LLM 实测**（**开工前需用户确认 key**，照 M2 issue 07 流程）：OpenAIPlannerClient 经 infra 收口（C4+C5；`infra/llm.py` 已 283 行，拆分落位本票内过评审后执行——风险清单 #5）；3 剧本真实调用回填步数/耗时/成本（usage × R9 单价）与结论
- **收尾**：设计文档验收节逐条回填（禁虚构）→ 翻 `implemented`；`CONTEXT.md` 新术语入表核对；`decisions.md` D-22+ 落位核对；spec 状态节更新；失败模式畸形率记录（风险 #1：>10% 触发 G1 复议）

## 要点

- 全程只碰 `datasets/golden/dev/`（holdout/ 禁读含 raw 切片）
- golden 数据作 mock 工具夹具来源（把 golden timeline 转成 Fetcher 替身的返回），保证「查到的证据」与剧本同源（D-18 纪律）
- 真实调用零改测试：`MockPlanner` → `OpenAIPlannerClient` 热切换，契约测试不变
- 门禁基线不回退：280 passed / 4 skipped / 97.12%（随新增测试自然上涨）

## 验收（可机械判定）

- [ ] 3 剧本 mock 端到端 conclusion 且根因规则匹配级命中（逐剧本记录）
- [ ] 步数 ≤15 / 总时长 ≤5min 实测回填（mock 期即可测）
- [ ] 真实 LLM 实测回填：步数/耗时/成本/结论 + usage 明细（¥0.5 上限比对）
- [ ] 设计文档验收节全部打勾、翻 `implemented`；CONTEXT/decisions 核对完成
- [ ] 全量 pytest + ruff 双检绿；coverage ≥80%
