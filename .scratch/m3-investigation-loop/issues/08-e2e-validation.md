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

## Comments（T8 执行期注记）

### 2026-09-08 开工门槛与拆分方案

- **key 门槛已解除**：用户确认 `ONCALL_LLM_*` env 可用（`~/.oncall-llm-env` 三件套）并授权真实调用（预估单次 ≈¥0.02，上限 ¥0.5）。A/B 两段连续执行。
- **`infra/llm.py` 拆分方案（票面要求先注记再动手）**：`llm.py` 现 283 行贴 C6，真实 Planner client 落新文件 `src/oncall/infra/llm_planner.py`（`OpenAIPlannerClient` + usage 留存 + prompt 组装，模块级模板常量照 `classify/llm/prompt.py` 先例）；复用 `llm.py` 的 `LLMClientConfig`/`LLMConfigError`/`LLMUsage`/`ChatClient`/`JSON_MODE`/`DISABLE_THINKING`/`_usage_of`（同包 import，不复制第二份）；pyproject C4+C5 `ignore_imports` 扩 `oncall.infra.llm_planner -> openai`。`llm.py` 本体零改动（283 行不动）。
- **异常族语义**（踩坑⑨对齐）：SDK 畸形输出（空/非 JSON/契约不过）→ `PlannerOutputError`（loop 重试 ≤2）；`APITimeoutError` → `PlannerTimeoutError`（不重试）；其余 `OpenAIError` → 基类 `PlannerError`（不重试，冒泡到 API 层 500 结构化兜底）——与 M2 `LLMClassifierError`「非畸形不重试」语义一致。infra import `oncall.harness.planner` 异常族方向合法（C3 只限 harness → 业务模块）。
- **B 段取证面**：真实 Planner 消费的证据仍取 golden timeline 同源 Fetcher 替身（D-18；只含 alert_timeline/labels/时间窗，**不含 root_cause/investigation_path/remediation**——避免把答案喂给被评模型）；判分器（规则匹配级：关键实体 + 动作词）与 mock 夹具共享落 `tests/golden_support.py`，A/B 两段同一判分口径。
