Status: resolved
Blocked by: 05, 06, 07

# 08 真实实测重跑与收尾（T8；真实调用开工前需用户确认 key）

> **开工门槛（照 M3 issue 08 流程）**：真实 LLM 调用前需用户确认 key（`ONCALL_LLM_*` env / `~/.oncall-llm-env` 三件套）并授权预算（M3 实测口径：两轮 6 次合计 ≈¥0.008，单次最高 ¥0.004，R9 单价，来源 issue 08 注记 2026-09-08）。

## 任务

- **两缺口修复后重跑真实实测**：3 剧本（cpu-spike / slow-sql / queue-backlog）真实调用（qwen3.7-flash，JSON Mode），回填步数/耗时/成本/结论
- **Top-1 如实记录**（禁虚构；口径复核归 M7）——与 M3 基线（首版 0/3，主失败 = 参数级失败 + 同参绕圈）对比：opening 视图（05）与 schema 摘要（06）修复后的改善幅度是本票核心观测
- 参数级失败率与畸形率（JSON 契约）对比 M3 基线记录；usage 明细落 `.scratch/tmp/`（不进版本库）
- **收尾**：设计文档验收节逐条回填 → 翻 `implemented`；`CONTEXT.md`/`decisions.md` 落位核对；spec 状态节更新；架构 §4 六表→七表回写与 agent-loop-design 修订（03 已带）落位终核

## 要点

- 真实调用零改测试：`MockPlanner` → `OpenAIPlannerClient` 热切换，契约测试不变（M3 先例）；证据取 golden timeline 同源 Fetcher 替身（D-18，不含 root_cause/investigation_path/remediation）
- 落库链路在真实轮全程生效——真实轮即「100% 落库」的实战验证
- 成本逼近 ¥0.5 上限即停手上报（实测基线远低于该值，预期不触发）

## 验收（可机械判定）

- [x] 3 剧本真实实测回填完整：步数/耗时/成本/Top-1/参数级失败率/畸形率（逐剧本记录，禁虚构）——两轮 6 次全记录，usage 明细落 `.scratch/tmp/m4-08-llm-e2e-report.json`
- [x] 与 M3 基线对比结论落 Comments（改善或未改善均如实记录；未改善须分析归因并交用户复议）——Top-1 0/3 未改善已归因并交复议（2026-09-08 用户拍板按票面收尾）
- [x] 设计文档验收节全部打勾、翻 `implemented`；CONTEXT/decisions 落位核对完成——D-30–D-38 均在 decisions.md；CONTEXT 无新增术语（测量替身/计数代理属测试实现细节，非领域概念）；架构 §4 七表回写 T8 终核补落
- [x] 全量 pytest + ruff 双检绿；coverage ≥80%——541 passed / 10 skipped / coverage 97.88%（+3 skipped = 本票 harness 花钱开关默认跳过；passed+skipped 总量 551 ≥ 548 不减）

## Comments（T8 执行期注记）

### 2026-09-08 开工门槛解除

- 用户确认 `~/.oncall-llm-env` 三件套就位（`ONCALL_LLM_*`），授权按 M3 口径预算开跑（两轮 6 次合计 ≈¥0.008 预估、单次最高 ¥0.004、R9 单价，逼近 ¥0.5 上限停手）。
- harness 落 `tests/integration/test_m4_real_rerun.py`（新文件，既有测试零改动）：照 M3 先例组装「真实 Planner + golden Fetcher 替身 + Verifier（裁决接缝留 mock，default=证实）+ EvidenceRepository」经 `POST /investigate` 走 3 剧本；测量替身 = Planner/Registry 外层计数代理（只观测不改行为）——畸形率 = PlannerOutputError 占比、参数级失败率 = ToolParamError/UnknownToolError 占比；落库链路全程生效（100% 落库三表对账 + opening_card 留存 + GET 出口 roundtrip 逐次断言）。
- git 基线确认 `e24c2c3`，工作区干净；落库全程内存 SQLite（勿碰根 `oncall.db`）。

### 2026-09-08 实测回填（两轮 6 次，qwen3.7-flash，R9 单价，合计 ≈¥0.0111 / 40 次调用）

**轮 1**

| 剧本 | termination | 步数 | 耗时 | 成本 | Top-1 | 主失败 |
|---|---|---|---|---|---|---|
| cpu-spike | concluded | 2 | 3.8s | ¥0.000814 | ✗ | 收束退行文本（「无任何证据输入」类）+ 1 次输出畸形（畸形率 0.25） |
| slow-sql | escalated | 2 | 2.2s | ¥0.000512 | ✗ | 同参绕圈（get_topology）熔断 plan_error |
| queue-backlog | escalated | 15 | 17.7s | ¥0.006685 | ✗ | 步数上限 15 耗尽；参数级失败率 15.8%（3 次 ToolParamError） |

**轮 2**

| 剧本 | termination | 步数 | 耗时 | 成本 | Top-1 | 主失败 |
|---|---|---|---|---|---|---|
| cpu-spike | escalated | 2 | 2.2s | ¥0.000533 | ✗ | 同参绕圈（get_topology）熔断 plan_error |
| slow-sql | escalated | 7 | 8.8s | ¥0.001871 | ✗ | 同参绕圈（query_kb）熔断 plan_error |
| queue-backlog | escalated | 2 | 3.7s | ¥0.000641 | ✗ | 同参绕圈（get_topology）熔断 plan_error |

- 逐次全部记录、未重跑挑数；escalated 语义照 D-28（转人工不是丢弃）——6 次调查证据链全部落库可查。
- usage 明细落 `.scratch/tmp/m4-08-llm-e2e-report.json`（不进版本库）。

### 2026-09-08 与 M3 基线对比及归因（基线出处：M3 issue 08 注记，首版 0/3、≈¥0.008/6 次、步数 2–5、主失败 = 参数级失败 + 同参绕圈）

| 维度 | M3 基线 | M4 重跑（两轮 6 次） | 判定 |
|---|---|---|---|
| Top-1 | 0/3 | 0/3 | **未改善** |
| 参数级失败 | 主失败之一 | 6 次中仅轮 1 queue-backlog 出现（3/33 选工具决策 ≈9%） | **改善**（D-38 schema 摘要直击该失败模式，兑现预期） |
| 同参绕圈 | 主失败之一 | 6 次中 4 次熔断于此（get_topology×3 / query_kb×1） | **未改善**（缺口①②不针对此模式） |
| 畸形率 | ≈0% | 1/40 = 2.5%（轮 1 cpu-spike 1 次 PlannerOutputError） | 持平；不触发 G1 复议（>10% 线） |
| 落库链路 | 内存态 | 6 次全部 100% 落库断言绿 + opening_card 留存 + GET roundtrip | **结构面目标达成** |

**归因（Top-1 未改善）**：

1. **主因不是上下文结构**：opening 视图（D-37）与 schema 摘要（D-38）已生效——参数级失败确证下降、轮 1 cpu-spike 能正常 concluded（2 步），说明两缺口修复有效；但 flash 级模型在有证据步的情况下仍收束出「无证据」退行文本（轮 1 cpu-spike）、或围绕 get_topology/query_kb 同参打转——**模型决策质量问题，归 M7 复核口径**（LLM-as-judge + 人工抽检 20%；规则匹配级判分口径复核亦归 M7）。
2. **绕圈是残留主失败**：D-27 熔断语义按设计工作（警告 → 再犯熔断转人工），D-38 管「看不见参数」管不住「看见参数仍打转」；若 M7 评测要改善 Top-1，候选方向 = 换模型档位 / 视图层绕圈引导 / 判分口径校准——均属新决策点，不在本票范围。
3. **结构面零回归**：100% 落库、可回溯、JSON/Markdown 导出、两缺口修复、门禁只增不减全部可判绿——M4 交付目标达成。

**复议结论（2026-09-08）**：用户拍板**按票面收尾**——设计文档翻 `implemented`，Top-1 0/3 与绕圈问题留 M7 复核口径，不另开 issue。

### 交付物与落位核对

- `tests/integration/test_m4_real_rerun.py`（真实轮 harness，花钱开关 `ONCALL_RUN_LLM_E2E=1`；Planner/Registry 计数代理只观测不改行为；生产代码零改动、既有测试零改动）
- 落位核对：D-30–D-38 均在 `decisions.md`；`CONTEXT.md` 无新增术语（调查记录/证据仓库已随评审入表）；架构 §4 七表条目（`investigations`）T8 终核补落；agent-loop-design 示例键名修订（T3 已带）终核 ✅；spec.md 08 行翻 resolved
- **M4 里程碑 8/8 resolved 收官**

### 遗留风险清单（移交 M7，2026-09-08 收官时登记）

1. **Top-1 复核口径**：规则匹配级判分（D-29）0/3 可能存在判分宽严问题——M7 须以 LLM-as-judge + 人工抽检 20% 复核后再下结论（本票不改判分口径）。
2. **同参绕圈失败模式**：6 次中 4 次熔断于此（get_topology×3 / query_kb×1）；三候选方向 = 换模型档位 / 视图层绕圈引导 / 判分口径校准，需 M7 评测数据支撑后立决策点（复议结论：不随本票另开 issue）。
3. **holdout 同步**：D-21 的 `HOLDOUT_SYNC_PENDING` 过渡豁免仍挂起，M7 前须同步 holdout 后清空还原硬约束（golden 树校验 R2 成对规则恢复全量）。
