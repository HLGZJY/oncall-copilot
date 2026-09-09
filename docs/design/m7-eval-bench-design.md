---
title: "M7 评测台（剧本 runner + 指标矩阵 + 模型对比 + 回归）：设计与开发计划"
summary: "M7 设计草案（draft，待评审）。对黄金集剧本全自动跑 N 遍产出指标矩阵：Top-1/Top-3 命中、步数、耗时、成本、失败模式归类；≥2 LLM 模型矩阵对比（env 切换面）；每次代码变更跑回归（Makefile/CI 面）。判对错两级（规则匹配 → LLM-as-judge + 人工抽检 20%）；复用出口与 escalated 案例按 D-57/D-56 口径单列。G1–G8 开放点预填推荐解待用户拍板"
source: docs/prd.md §7-M7 + docs/architecture/architecture.md（时序 §5 / 数据 §4）+ docs/design/decisions.md D-28/49-57 + docs/reference/_sources/项目信息.md 3.3 模块 F + 6.5（只回溯：评测台方法论与防泄漏）+ src/oncall/harness/loop.py（FailureMode 六值 / InvestigationResult 现状接缝）+ src/oncall/infra/llm_planner.py（ONCALL_LLM_* 切换面）+ src/oncall/classify/stats.py（指标核算纯函数，M2 issue 05 产出）+ tests/integration/test_m5_real_e2e.py（真实 e2e 开关先例）+ 评审标准来源（见「评审依据」R1–R6）
status: draft
updated: 2026-09-09
read_when: 评审 M7 方案时；进入 M7 开发前；被问「评测怎么判对错/模型怎么对比/评测怎么防泄漏」时
---

# M7 评测台（剧本 runner + 指标矩阵 + 模型对比 + 回归）：设计与开发计划

## status

`draft`（草案，讨论中）→ `reviewed`（评审通过，可拆票）→ `implemented`（已落地，实测回填）→ `superseded`

- **当前状态**：`draft`（2026-09-09 草案完成，待用户逐条拍板 G1–G8）
- **评审人 / 评审日期**：（评审通过后回填）
- **设计期口径**：本票零写码、零建表、零真实 LLM 调用。评测台单元测试全走 mock（MockPlanner / MockClassifier）；真实调用走显式 env 开关（`ONCALL_RUN_M7_EVAL=1` 惯例）+ **key 门槛票单独拍板**（D-50 先例）；holdout/ 只做最终评测、评测前禁读（防泄漏纪律，M2 issue 03/05 先例）。

## 目标

- **背景 / 触发原因**：M6 收官（D-49–D-57 落地）。M7 = PRD §7-M7「评测台」（**P1 差异化三支柱之一，不可砍**）——面试硬口径「≥2 模型 × ≥8 剧本矩阵报告」的兑现票；decisions.md 待定项「评测判对错细则」须在本票内敲定（评审 G 面）。
- **要解决的问题**：
  1. **可回归的评测设施**：对黄金集剧本（预标注根因/期望处置）全自动跑 N 遍，产出 Top-1/Top-3 命中、步数、耗时、成本、失败模式五列指标矩阵——Agent 行为改动后跑一条命令即可回归；
  2. **判对错两级**（AGENTS 硬规 7）：规则匹配兜底全量 → LLM-as-judge 复核 + 人工抽检 20%；失败模式**强制归类不丢弃**（归不进六值的落 unknown 强制人工归类）；
  3. **模型矩阵**：≥2 个 LLM（技术栈地图 DeepSeek/Qwen 同族，经 `ONCALL_LLM_*` env 切换面装配，零硬编码）同集对比，产出选型理由；
  4. **口径纯净**（消费 D-56/D-57）：复用出口（`reused_from`）不计入调查指标、单列复用命中率；escalated 案例走人工标注通道单列。
- **不做的事（Non-goals）**：
  - **不做 M8 展示**：README 挂图只要求评测报告 markdown 存在；报告页/图表美化归 M8
  - **不做 M9 展示层**：M9 若并入，本票**只做成本列埋点**（total_tokens/total_cost_cny 已在 InvestigationResult，实测/估算分列沿用 llm.py 口径），每步 trace 可视化归 M8/M9
  - **不改 D-23 六工具集合、不改 harness 契约面**：runner 只装配既有组件（LoopComponents），不扩接口
  - **不推翻防泄漏纪律**：few-shot 只取 dev 且排除 3 验证剧本；holdout 只做最终评测，开发期禁读
  - **设计期零真实调用**：所有 LLM 相关测试 mock-only

## 技术方案

**一句话概括**：`src/oncall/eval/` 新模块——golden 加载器（dev/holdout 双集隔离）→ 剧本 runner（进程内装配 LoopComponents 直跑，mock/真实双档）→ 判对错管线（规则匹配全量 → LLM-as-judge 复核 → unknown 人工通道）→ 指标核算（复用 `classify/stats.py` 纯函数口径 + 新增命中/步数/耗时/成本核算）→ `eval_runs` 第十表落明细 + JSON 明细/汇总 markdown 双产物 → `make eval` 一键回归。

### 设计的模块

| 模块 | 动作（新增/修改） | 职责 | 关联里程碑 |
|---|---|---|---|
| `src/oncall/eval/golden.py` | 新增 | 黄金集加载器：dev/holdout 双集加载 + 防泄漏守卫（few-shot 源排除断言、holdout 显式解锁开关） | M7 |
| `src/oncall/eval/runner.py` | 新增 | 剧本 runner：装配 LoopComponents 对每剧本跑 N 遍，产出 `EvalRun` 明细（复用 `InvestigationResult` 现有列） | M7 |
| `src/oncall/eval/judging.py` | 新增 | 判对错两级：规则匹配层（结论关键词/假设规范化比对，复用 `normalize_hypothesis_text`）+ judge 契约（Protocol，mock 可注换） | M7 |
| `src/oncall/eval/metrics.py` | 新增 | 指标核算：Top-1/Top-3、步数/耗时/成本均值±方差、失败模式归类、复用/escalated 单列（降噪指标直接复用 `classify/stats.compute_denoise_metrics`） | M7 |
| `src/oncall/eval/report.py` | 新增 | 报告产出：JSON 明细 + 汇总 markdown（矩阵表 + 选型理由骨架） | M7 |
| `src/oncall/db/` | 修改 | `eval_runs` 第十表（显式偏差，随实现票回写架构 §4） | M7 |
| `Makefile` / CI | 修改 | `make eval`（mock 档回归）+ `make eval-real`（真实档，env 开关） | M7 |

### 数据模型变更

| 变更项 | 类型 | 说明 | 迁移方式 |
|---|---|---|---|
| `eval_runs` 第十表 | 新增 | 一次评测运行一行：`id/scenario/set(dev\|holdout)/model/run_idx/verdict(top1\|top3\|miss)/failure_mode/step_count/duration_s/tokens/cost_cny/judged_by(rule\|judge\|human)/reused/escalated/run_json/created_at` | `create_all`（D-13/D-30 先例，无 Alembic） |

> CONTEXT.md「调查记录」条已预留「M7 `eval_runs` 是评测行，不与 investigations 混表」——本票兑现该预留。落库与 JSON 文件的关系见 G5。

### API 变更

| API | 变更类型 | 请求/响应要点 | 影响调用方 |
|---|---|---|---|
| 无 REST 新端点 | — | 评测是离线批处理设施，不进 API 面；CLI 入口 `python -m oncall.eval` + Makefile 目标 | Makefile / CI |

## 评审决策点（G 面，预填推荐解待拍板）

> 评审通过后逐条登记 `decisions.md` 接 **D-58+**；用户可推翻推荐解，推翻则回退对应 issue 重开。

### G1 Runner 形态：进程内直跑 vs HTTP 打活栈

- **推荐解**：**进程内 harness 直跑**（测试/CI mock 档：MockPlanner 驱动 LoopComponents；真实档：`ONCALL_RUN_M7_EVAL=1` + 真实 planner/chaos 注入，复用 M5/M6 真实 e2e 的 LiveServer + chaos 注入面先例）。
- **理由**：mock 档要在 CI 秒级回归，HTTP 打活栈做不到；真实档与 M5 issue 08 的 `ONCALL_RUN_*` 惯例同构，环境门槛显式化。

### G2 判对错两级：规则匹配 → LLM-as-judge 契约

- **推荐解**：**规则匹配兜底全量**（根因关键词比对 + `normalize_hypothesis_text` 规范化后与黄金集 `root_cause` 假设做规范化包含判定，命中即 top1/top3）；规则未命中的样本交 **LLM-as-judge**（judge 模型经 `ONCALL_LLM_*` env 独立配置，与被评模型解耦——避免模型给自己打分）；judge 契约（输入=结论+证据摘要+黄金标注，输出=`{verdict, reason}` 结构化 JSON）**先冻结 mock 实现**，真实 judge 是 **key 门槛票**（D-50 先例）；人工抽检 20%（硬规 7），抽检记录回填 issue Comments。
- **理由**：规则层确定性可测、零成本；judge 只处理模糊地带（母本「判对错两级」原意）；契约先冻结使 mock-only 纪律不破。

### G3 成本核算口径与 M9 并入边界

- **推荐解**：**只做成本列埋点、不做展示层**（PRD 允许 M9 并入 M7）。数据源两列分列不混算（llm.py 既有口径）：`tokens/cost_cny` 用 InvestigationResult 既有合计（G8 估算法），`usage_log` 实测 usage 另列。展示归 M8/M9。
- **理由**：成本列已是矩阵验收口径（五列指标），埋点即够；展示层是 M9 的差异化独立叙事，现在做会摊薄 W6 冲刺。

### G4 N 遍与 flaky 容忍口径

- **推荐解**：**每剧本每模型 N=3，报均值±极差**；同剧本 3 遍判定不一致 → 该行标 `unstable`（单列不静默平均）；失败模式按多数归类，无多数落 unknown。真实档预算：12 dev 剧本 × 2 模型 × 3 遍 ≈ 72 次调查，按 ≤¥0.5/次上限估 ≤¥36，可接受；超预算时裁剪方案按 PRD ③（降单模型）走。
- **理由**：N=1 会把 LLM flaky 当真实回归；N=5 成本翻倍收益边际；极差比方差对小样本更直观。

### G5 报告产物与落点

- **推荐解**：**双产物**——① `eval_runs` 表 = 明细权威（可 SQL 审计、可复算）；② `datasets/eval/` 下导出 `runs-<date>.json`（机器可读）+ `report-<date>.md`（人读矩阵表，README 挂图引用此文件）。`datasets/eval/` 加入 git（评测报告是面试资产，须可追溯）。
- **理由**：与 D-49「权威单源」哲学一致——表是权威，导出是序列化副本；落 `.scratch/` 会被当临时物，落 `datasets/` 与黄金集同域。

### G6 失败模式归类规则

- **推荐解**：**自动归类规则链 + unknown 强制人工**。优先级：循环已给六值（`InvestigationResult.failure_mode`，D-28 机械可判）直接消费 → 判定 miss 但 failure_mode 为空（premature_stop 预标注被复核推翻等）→ 规则归 `no_signal`/`plan_error`（证据步有无信号/步数分布判定）→ 归不进六值落 `unknown`，**禁丢弃**，进人工归类通道（issue Comments 登记后回填）。
- **理由**：六值已是主循环契约（M3–M5 已落地），M7 只做消费与兜底，不重造归类器；unknown 通道兑现「强制归类不丢弃」。

### G7 复用出口与 escalated 案例的指标口径

- **推荐解**（消费 D-57/D-56）：**复用出口不计入 Top-1/Top-3 命中分母**——剧本运行中命中指纹复用（`reused_from`）的行单独核算「复用命中率 = 复用行数 / 可复用机会数」单列，证明缓存有效性；**escalated 案例不计入失败**，单列 `escalated` 列 + 走 D-56 人工标注通道（标注根因后可回补为正常样本）。降准/漏报不得被复用与 escalated 稀释。
- **理由**：D-57 已明确 hit_count 口径分离，M7 延伸到矩阵列分离；escalated = 转人工不是丢弃（D-28），计入失败会冤枉系统。

### G8 模型矩阵选型与切换面

- **推荐解**：**DeepSeek + Qwen 同族对比**（技术栈地图既定两候选），经 `ONCALL_LLM_BASE_URL/MODEL/API_KEY` env 驱动装配（`OpenAIPlannerClient.from_env` 现成切换面，零代码改动）；矩阵 runner 以「模型 profile」参数化（每个 profile 一组 env 前缀），**不硬编码模型名进代码**。具体档位（deepseek-chat vs qwen3.7-flash 等实际型号与单价）在 key 门槛票拍板时用实测单价回填。
- **理由**：切换面已是 env 驱动（llm_planner.py D-19 面），矩阵只是多次装配；硬编码模型名会让第三次对比成为代码改动。

## 评审依据（R 面）

> 以项目内权威为主；外部标准（LLM-as-judge 实践、评测防泄漏）如评审时需要，按需检索补充 URL 与取用日期（M6 R1–R5 同款格式）。

- **R1**：`docs/prd.md` §7-M7 + §5 模块 F（评测台）+ 6.5 防泄漏——需求与方法论权威
- **R2**：`docs/design/decisions.md` D-28（六值归类/收尾结构）、D-49（权威单源哲学）、D-50（key 门槛票先例）、D-56（escalated 人工标注通道）、D-57（复用口径分离）
- **R3**：`AGENTS.md` 硬规 7（判对错两级/失败模式强制归类/回归纪律）、硬规 10（实测回填禁虚构）
- **R4**：`src/oncall/harness/loop.py`——`FailureMode` 六值、`InvestigationResult`（step_count/total_tokens/total_cost_cny 现成列）、`normalize_hypothesis_text`
- **R5**：`src/oncall/infra/llm.py` / `llm_planner.py`——`ONCALL_LLM_*` env 切换面、usage 实测/估算分列口径
- **R6**：`.scratch/m2-noise-reduction/issues/05-stats-logical-alerts.md`——`compute_denoise_metrics` 纯函数「输入评测运行数据 → 输出指标字典」，降噪列直接复用勿重造；M2 issue 03/05 dev/holdout 防泄漏先例

## 验收标准（可实测，禁虚构；上线后回填）

- [ ] `make eval`（mock 档）一键跑通 dev 12 剧本 × N=3，产出 JSON 明细 + 汇总 markdown，进 CI 回归
- [ ] 指标矩阵五列齐全：Top-1/Top-3 命中、步数、耗时、成本、失败模式；复用命中率与 escalated 各单列
- [ ] ≥2 模型矩阵对比报告产出（README 挂图），含选型理由（真实档，key 门槛票放行后）
- [ ] 失败模式 100% 归类（六值或 unknown，unknown 全部走人工通道有记录）
- [ ] 防泄漏守卫测试绿：holdout 未显式解锁时加载即 fail；few-shot 源含 3 验证剧本即 fail
- [ ] `eval_runs` 表落明细，报告每个数字可回溯到表行（禁虚构口径）
- [ ] 全量 pytest（门禁基线只增不减）+ ruff 双检 + import-linter + bandit 绿

## 依赖

- **前置依赖**：M2–M6 已落地（LoopComponents 装配面 / 六值归类 / stats.py / D-56/D-57 口径）；评审 G1–G8 拍板
- **数据 / 环境依赖**：`datasets/golden/dev/`（12 剧本，已双签）；holdout（最终评测前禁读）；真实档需 LLM key（key 门槛票）+ 活 demo 栈 + chaos 工具
- **后续影响**：M8 README 挂图消费本票报告；M9（若独立）消费本票成本埋点与 trace 面

## 任务拆票预告（评审定稿后按编号派发）

| Issue | 任务 | Blocked by | 就绪态 |
|---|---|---|---|
| 01 | `eval_runs` 第十表 + eval 模块骨架 + golden 加载器（防泄漏守卫） | — | needs-triage |
| 02 | 剧本 runner（LoopComponents 装配 + mock/真实双档） | 01 | needs-triage |
| 03 | 判对错管线（规则匹配层 + judge 契约 mock 冻结） | 01 | needs-triage |
| 04 | 指标核算（命中/步数/耗时/成本/失败模式 + 复用/escalated 单列，复用 stats.py） | 03 | needs-triage |
| 05 | N 遍矩阵 + 模型 profile 矩阵 + 报告双产物 | 02,04 | needs-triage |
| 06 | Makefile/CI 回归收口 + 架构 §4 回写（第十表）+ 文档 | 05 | needs-triage |
| 07 | 真实评测（key 门槛票 + 活栈环境门槛，2 模型 × dev 集 + holdout 最终评测） | 06 | needs-triage（附门槛） |
