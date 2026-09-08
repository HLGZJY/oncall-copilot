Status: resolved
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

- [x] 3 剧本 mock 端到端 conclusion 且根因规则匹配级命中（逐剧本记录）——3/3 命中（`tests/e2e/`，D-18 替身证据不含 root_cause）
- [x] 步数 ≤15 / 总时长 ≤5min 实测回填（mock 期即可测）——mock 各 3 步 / <0.2s；真实轮 ≤5 步 / ≤8.0s
- [x] 真实 LLM 实测回填：步数/耗时/成本/结论 + usage 明细（¥0.5 上限比对）——两轮 6 次合计 ≈¥0.008；Top-1 首版 0/3 如实记录（口径复核归 M7），两个结构缺口见下方注记
- [x] 设计文档验收节全部打勾、翻 `implemented`；CONTEXT/decisions 核对完成——CONTEXT 无新增术语（判分口径即 D-29 既有词）；D-22–D-29 落位核对无误
- [x] 全量 pytest + ruff 双检绿；coverage ≥80%——479 passed / 7 skipped / 98.05%（基线 462→479 只增不减；7 skipped = 基线 4 + 真实 e2e 花钱开关默认跳过 3）

## Comments（T8 执行期注记）

### 2026-09-08 开工门槛与拆分方案

- **key 门槛已解除**：用户确认 `ONCALL_LLM_*` env 可用（`~/.oncall-llm-env` 三件套）并授权真实调用（预估单次 ≈¥0.02，上限 ¥0.5）。A/B 两段连续执行。
- **`infra/llm.py` 拆分方案（票面要求先注记再动手）**：`llm.py` 现 283 行贴 C6，真实 Planner client 落新文件 `src/oncall/infra/llm_planner.py`（`OpenAIPlannerClient` + usage 留存 + prompt 组装，模块级模板常量照 `classify/llm/prompt.py` 先例）；复用 `llm.py` 的 `LLMClientConfig`/`LLMConfigError`/`LLMUsage`/`ChatClient`/`JSON_MODE`/`DISABLE_THINKING`/`_usage_of`（同包 import，不复制第二份）；pyproject C4+C5 `ignore_imports` 扩 `oncall.infra.llm_planner -> openai`。`llm.py` 本体零改动（283 行不动）。
- **异常族语义**（踩坑⑨对齐）：SDK 畸形输出（空/非 JSON/契约不过）→ `PlannerOutputError`（loop 重试 ≤2）；`APITimeoutError` → `PlannerTimeoutError`（不重试）；其余 `OpenAIError` → 基类 `PlannerError`（不重试，冒泡到 API 层 500 结构化兜底）——与 M2 `LLMClassifierError`「非畸形不重试」语义一致。infra import `oncall.harness.planner` 异常族方向合法（C3 只限 harness → 业务模块）。
- **B 段取证面**：真实 Planner 消费的证据仍取 golden timeline 同源 Fetcher 替身（D-18；只含 alert_timeline/labels/时间窗，**不含 root_cause/investigation_path/remediation**——避免把答案喂给被评模型）；判分器（规则匹配级：关键实体 + 动作词）与 mock 夹具共享落 `tests/golden_support.py`，A/B 两段同一判分口径。

### 2026-09-08 实测回填（A 段 + B 段，key 门槛已由用户确认解除）

**A 段 mock 端到端（3/3 命中，`tests/e2e/` 默认门禁）**

| 剧本 | termination | 步数 | 时长 | failure_mode | 判分 |
|---|---|---|---|---|---|
| cpu-spike | concluded | 3 | 0.146s | None（末步假设证实） | 命中 |
| slow-sql | concluded | 3 | 0.063s | None | 命中 |
| queue-backlog | concluded | 3 | 0.083s | None | 命中 |

MockPlanner 剧本自 golden `investigation_path` 派生，收束结论 = golden `root_cause` 逐字（同源自证，验证判分器与链路本身）；裁决剧本前 n-1 步证伪、末步证实。

**B 段真实 LLM 实测（qwen3.7-flash，两轮 6 次，R9 单价计费）**

| 轮次 | 剧本 | termination | 步数 | 耗时 | 成本 | 调用数 | tokens | Top-1 |
|---|---|---|---|---|---|---|---|---|
| 1 | cpu-spike | concluded | 2 | 27.4s | ¥0.0039 | 14 | 12230 | ✗ |
| 1 | slow-sql | concluded | 2 | 2.7s | ¥0.0004 | 3 | 1287 | ✗ |
| 1 | queue-backlog | concluded | 2 | 4.0s | ¥0.0006 | 4 | 2034 | ✗ |
| 2 | cpu-spike | escalated | 2 | 2.2s | ¥0.0004 | 3 | 1175 | ✗ |
| 2 | slow-sql | concluded | 2 | 4.7s | ¥0.0008 | 5 | 2383 | ✗ |
| 2 | queue-backlog | escalated | 5 | 8.0s | ¥0.0015 | 8 | 4702 | ✗ |

- **合计成本 ≈¥0.008，远低于 ¥0.5 上限**；步数/时长均在限内。
- **Top-1 首版 0/3（如实记录，禁虚构）**：结论均为「调查中止/缺上下文」类退行文本，无有效根因。详细 usage 明细落 `.scratch/tmp/m3-08-llm-e2e-report.json`（不进版本库）。
- **畸形率（风险 #1 口径）**：JSON 契约畸形（PlannerOutputError 触发重试）≈0%——诊断 trace 中未出现输出畸形重试；主失败模式是**参数级失败**（工具入参盲猜不过 schema 校验，notice 消耗轮次）与**同参绕圈熔断**（get_topology/query_kb 重复调用）。**不触发 G1 复议**（>10% 指的是输出畸形率），但参数失败高企与下述缺口②直接相关，修复后需重测。

**两个结构缺口（harness 冻结，本票停手注记，建议随 M4 修复后重跑实测）**

1. **Planner 视图缺事件锚点**：`harness/planner.py` 接缝 docstring 承诺视图 =「记忆摘要 + 事件锚点（D-25 口径）」，但 `loop._decide_with_retry` 实装 view 仅 `{system_prompt, steps, hypotheses, notices}`——模型开局面盲（两轮结论均直呼「未提供告警关联的服务名称及时间窗口」），开局只能盲选 get_topology。这是硬规 6「可观测数据质量决定 AI 上限」的实证：先补上下文，再谈模型能力。修复建议：view 增 `opening`（D-17 卡片精简投影），注意 C6 行数预算（loop.py 已 297 行）。
2. **工具入参 schema 不可见**：架构 §3.3 承诺「模型可调 tool_help 查详情」的 just-in-time 通道，但 D-23 冻结六工具未含 tool_help，system prompt 只有名称+一句话描述——query_metrics 必填 start/end 全靠盲猜，参数失败后 notice 循环消耗步数。修复建议（二选一，需小评审）：system prompt 附六工具入参 schema 摘要，或补第 7 个 tool_help 工具（改 D-23 面）。

**交付物**：`src/oncall/infra/llm_planner.py`（真实 Planner client，153 行，C4+C5 ignore_imports 已扩）+ `tests/unit/test_infra_llm_planner.py`（14 契约单测，mock transport）+ `tests/integration/test_planner_real_e2e.py`（花钱开关 `ONCALL_RUN_LLM_E2E=1`）+ `tests/golden_support.py`（A/B 共用夹具与判分器）。落位核对：D-22–D-29 均在 `decisions.md`；CONTEXT.md 无新增术语；架构守卫 LLM_ALLOWED / SDK_EXCEPTION_MAPPING_TESTS 已登记新文件。**M3 里程碑 8/8 resolved 收官**；M4 衔接：InvestigationSession 契约与 failure_mode/步数/成本字段即评测矩阵列，两结构缺口修复建议随 M4 规划派发。
