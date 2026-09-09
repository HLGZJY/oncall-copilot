# M7 评测台 — 设计文档派发 Prompt

> 用法：新会话整段粘贴。产出物 = `docs/design/m7-eval-bench-design.md`（状态 draft → 评审后才开工）。

---

## 任务

为 **M7 评测台（W6）** 撰写设计文档，落位 `docs/design/m7-eval-bench-design.md`（从 `docs/design/feature-design-template.md` 复制结构）。**本票只做设计，不写生产代码**；评审定稿（G 决策 + R 来源 + D 登记）后按编号 issue 派发开发。

## 范围（PRD §M7 验收硬口径）

- 剧本 runner：对每个剧本（预标注根因/期望处置）全自动跑 N 遍；
- 指标：Top-1/Top-3 命中、步数、耗时、成本、失败模式归类（tool_error / plan_error / timeout / hallucination / no_signal，见 AGENTS.md 硬规 7）；
- **≥2 个 LLM 模型矩阵对比**（技术栈地图 DeepSeek/Qwen，给出选型理由与切换面）；
- 每次代码变更跑回归（评测命令进 Makefile / CI 面）；
- 验收：评测报告产出（README 挂图），对比 2 模型给出选择理由。

## 必须消化的既有资产（设计前先读，禁止闭门造车）

| 资产 | 用途 |
|---|---|
| `docs/prd.md` §M7 + 模块 F（评测台）+ 6.5 防泄漏 | 需求权威与方法论母本 |
| `datasets/golden/dev/`（12 剧本）与 `holdout/`（11 剧本） | 已隔离的双集，**防泄漏纪律**：few-shot 只取 dev 且排除 3 验证剧本，holdout 只做最终评测（M2 issue 03/05 先例） |
| `.scratch/m2-noise-reduction/issues/05-stats-logical-alerts.md` | 指标核算纯函数「输入评测运行数据 → 输出指标字典」，M7 直接复用，勿重造 |
| `src/oncall/harness/loop.py` + `tests/integration/test_m5_real_e2e.py` / `test_m6_real_recall_e2e.py` | runner 的组件装配先例（LiveServer、ControlledExecutor、Verifier、真实 e2e 开关模式） |
| `docs/design/decisions.md` D-49–D-57 | M6 已定边界：**复用出口不计入调查指标**（D-57 hit_count 口径分离）、escalated 案例留 M7 人工标注通道（D-56）——设计时必须消费这两条 |
| `src/oncall/infra/llm.py` / `llm_planner.py` `from_env` | 模型矩阵的切换面（env 驱动，勿硬编码） |
| `.scratch/m6-report-kb/` 目录与 `docs/agents/issue-tracker.md` | tracker 与 issue 派发格式先例 |
| M3/M4/M5/M6 四份 design 文档 | 设计文档体例、G/R/D 命名法 |

## 设计必须回答的决策点（评审 G 面预填推荐解）

1. **Runner 形态**：进程内 harness 直跑（复用 LoopComponents）vs HTTP 打活栈？推荐前者 + chaos 注入面（M5/M6 真实 e2e 先例），mock/真实双档开关。
2. **判对错两级**（硬规 7）：规则匹配（根因指纹/结论关键词）→ LLM-as-judge + 人工抽检 20%；judge 用什么模型、契约怎么冻结（参照 D-50「key 门槛票」先例）。
3. **成本核算**：token 用量从 LLM client 埋点取（M9 若并入，需在本文档划边界——PRD 允许 M9 并入 M7，建议**只做成本列埋点、不做展示层**，展示归 M8）。
4. **N 遍与稳定性**：每剧本 N=?（推荐 N=3，报均值±方差）；LLM flaky 的容忍口径。
5. **报告产物**：JSON 明细 + 汇总 markdown/图表（README 挂图）；落 `.scratch/` 还是 `datasets/`？
6. **失败模式归类**：自动归类规则 + 归不进去的落 `unknown` 强制人工归类（禁丢弃）。
7. **复用出口与 escalalted 案例**在指标里怎么算/单列（D-57 口径）。

## 工作纪律（不可违反）

- **Agent 查事实，人做决策**：设计文档只对齐方案，评审通过前不写生产代码、不改接口；
- 术语一律用 `CONTEXT.md` 词汇，新术语当场入表；
- 新决策登记 `decisions.md` 接 D-58+；隐式偏差（如第九表式超规划）须显式登记并回写架构文档；
- mock-first：评测台单元测试零真实 LLM 调用，真实调用走显式 env 开关（`ONCALL_RUN_*` 惯例）+ key 门槛票单独拍板；
- 门禁：pytest（只增不减）+ ruff 双检；提交规范中文 + type 前缀，body 写为什么。

## 交付清单

1. `docs/design/m7-eval-bench-design.md`（status: draft，含 G1–Gn 决策点 + R 来源 + 接口草案）；
2. `.scratch/m7-eval-bench/` tracker 骨架（issue 标 `needs-triage`，等评审后升 ready）；
3. `CONTEXT.md` 新术语入表（如「评测运行」「黄金集防泄漏」「失败模式归类」若尚未定义）；
4. 结尾输出：待用户逐条拍板的 G 决策清单（含推荐解与理由，参照 M6 评审格式）。
