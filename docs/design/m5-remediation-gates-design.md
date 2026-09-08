---
title: "M5 处置与恢复验证（四道闸门）：设计与开发计划"
summary: "把 D-23 冻结的 execute_action L2 stub 实装为四道闸门处置管线：runbook（Markdown）解析为可执行动作 → 干跑（打印命令+影响面）→ 人工确认门（API 层拦截）→ 受控执行（命令白名单 + demo 容器内）→ 恢复验证（回查指标，未恢复自动回滚或转人工）；处置全程留痕并并入证据链报告；G1–G10 开放点评审与 T1–T8 拆票"
source: docs/prd.md §4/§7-M5 + docs/architecture/architecture.md §3.2/§3.3/§4/§5/§7 + docs/design/decisions.md D-16/17/19/22/23/25/28/30-38 + docs/conventions/security-guardrails.md（四道闸门）+ docs/conventions/quality-gates.md（C3/C4/C6/A6）+ docs/design/m4-evidence-chain-design.md（设计文档形制模板）+ .scratch/m3-investigation-loop/issues/08（execute_action stub 边界注记）+ chaos/scenarios/01-cpu-spike/02-slow-sql（cleanup 语义 = runbook 处置内容来源）+ 评审标准来源（见「评审依据」R1–R5）
status: draft
updated: 2026-09-09
read_when: 评审 M5 方案时；进入 M5 开发前；被问「写操作怎么保证可控/处置怎么自动化」时
---

# M5 处置与恢复验证（四道闸门）：设计与开发计划

## status

`draft`（草案，讨论中）→ `reviewed`（评审通过，可拆票）→ `implemented`（已落地，验收回填）→ `superseded`（被后续设计取代，注明替代文档链接）

- **当前状态**：`draft`（2026-09-09 草案完成，G 表待用户逐条拍板；**评审定案前不拆票、不登记 decisions**）
- **评审人 / 评审日期**：（评审通过后回填）
- **关联 issue**：`.scratch/m5-remediation-gates/`（spec.md + issues/01–08 与 T1–T8 一一对应，归评审后动作）
- **设计期口径**：本票零写码、零建表、零真实调用（mock-only LLM 纪律照 M2–M4 先例——处置管线是确定性系统行为，不经 LLM 裁决，故无真实 API 调用票；真实端到端需活 demo 栈，属环境门槛非 key 门槛，见 T8）

## 目标

- **背景 / 触发原因**：M4 8/8 resolved 收官（证据链三表落库 + GET 读库出口全绿）。M5 = PRD §7-M5「处置与恢复验证」——差异化闭环的第四段（分类→取证→**处置→恢复**），且是安全叙事（硬规 3「安全护栏落系统层」+ 硬规 4「写操作四道闸门缺一不可」）的落地载体。现状 `execute_action` 是 D-23 六工具之一的 **L2 stub**（PermissionGate 永远拒绝 + handler 返回 error 兜底），本票是**实装既有 stub**，不加第 7 工具、不改 D-23 冻结面。
- **要解决的问题**（PRD §7-M5 + §4「安全处置」验收展开）：
  1. **runbook 解析**：Markdown 定义的处置流程（动作/回滚/验证/前置条件）解析为可被 `execute_action` 引用的动作库——SOP「文档即代码」，模型决定何时调哪个 runbook（硬规 1：固定 SOP 只是工具）；
  2. **四道闸门实装**：干跑（第 1 门，打印命令+影响面不执行）→ 人工确认门（第 2 门，**API 层拦截**非提示词）→ 受控执行（第 3 门，命令白名单 + 仅 demo 容器内）→ 恢复验证（第 4 门，回查指标判恢复，未恢复自动回滚或转人工）；
  3. **证据链衔接**：处置的干跑预览/确认/执行/验证各节点留痕（PRD「全程留痕」），处置结论并入调查报告，未恢复的转人工出口对齐 D-28（转人工不是丢弃）。
- **承接的现状注记**（照 M4 承接 issue 08 注记的先例）：registry.py 注释「execute_action stub（D-23）：L2 由 PermissionGate 拦在执行前；此为纵深防御兜底」+ permission.py `LEVEL_DECISIONS[L2] = 永远禁止（M5 实装四道闸门前不开放）`——两处 docstring 都明确把「M5 实装」写成后续承诺，本票即兑现该承诺；**PermissionGate L2「永远禁止」语义变更为安全面变更，进 G3 评审**。
- **不做的事（Non-goals）**：
  - **不加第 7 工具、不改 D-23 冻结面**：六工具集合、`ExecuteActionInput{action, params}`、`ToolResult` 形状只消费不推翻（action/params 语义扩展见 G1/G3，须评审）
  - **不做 M6 知识库**：处置记录向量化入库、历史处置召回归 M6；本票只保证处置数据落库可取
  - **不做 M8 UI**：确认门 UI 交互（按钮/SSE）归 M8；本票的确认门 = REST API + 无 UI 阶段的最小形态（curl 可确认）
  - **不碰 incidents 冻结面**：`incidents` 五字段不扩列（D-19）；处置后 incident 翻 `mitigated` 属既有枚举值流转（架构 §4 枚举含 mitigated），不扩枚举
  - **不做 Alembic 迁移**：dev 单库延续 `create_all`（D-13/D-30 先例）
  - **不经 LLM 裁决处置**：处置管线是确定性系统行为；LLM 只在「要不要处置、调哪个 runbook」上做决策（即 Planner 的既有 execute_action 选择），确认后的命令清单由系统锁定执行
  - **设计期零写码**：不改 `src/`、`tests/`、`chaos/`、`datasets/`；`datasets/golden/holdout/` 禁读不变

## 本阶段涉及的技术类别

| 技术类别 | 涉及技术 | 本阶段用途 | 开放点 |
|---|---|---|---|
| runbook 文档契约 | Markdown + YAML frontmatter（零新依赖，stdlib `json`/自写 frontmatter 切分） | 处置流程文档 → 可执行动作库 | 格式契约字段集（G5） |
| 处置状态机 | 新模块 `oncall.remediation`（C3 禁列已预留） | 干跑提案 + 确认 + 执行 + 验证的状态流转 | 编排位置与会话语义（G1/G2） |
| 安全护栏 | PermissionGate L2 语义 + 命令白名单 + 受控执行器 | 系统层拦截写操作（硬规 3） | L2 放行条件（G3）/ 白名单登记（G4） |
| 恢复验证 | PromQL 回查（复用 `context/promql` Fetcher 接缝） | 处置后回查指标判恢复 | 指标判据与阈值来源（G6） |
| 回滚 | runbook rollback 定义 + 白名单执行 | 未恢复自动回滚或转人工 | 回滚动作来源（G7） |
| 证据链落库 | SQLAlchemy（M4 三表先例） | 处置各节点留痕 + 报告并入 | 留痕落点（G8，第八表显式偏差） |
| 工程门禁 | pytest TDD / ruff / import-linter C3–C6 / A6 bandit | 白名单校验/状态机/恢复判据是可机械断言接缝 | 测试策略沿用 M3/M4 方法（不设 G） |

## 技术方案

**一句话概括**：调查循环内 `execute_action` 从「L2 永远拒绝」实装为「处置请求工具」——Planner 调它时以**干跑模式**解析 runbook 动作、渲染「将执行的命令 + 影响面」并生成待确认处置提案（第 1 门）；人工经 `POST /remediations/{id}/confirm` 在 **API 层**批准（第 2 门）后，remediation 服务**确定性执行**提案锁定的命令清单——逐条过系统层命令白名单、仅 demo 容器内执行（第 3 门）——完成后按 runbook 声明的验证判据回查指标判恢复（第 4 门），恢复则 incident 翻 mitigated、未恢复执行 runbook 回滚、仍失败转人工；处置各节点留痕，调查报告并入处置记录。

### 处置主流程（四道闸门落位）

```
调查循环（Planner 自主决策，mock 决策脚本 / 真实 LLM）            remediation 服务（确定性，无 LLM）
┌──────────────────────────────────────────────┐              ┌──────────────────────────────────────────────┐
│ 根因取证 … → 决策调 execute_action              │              │                                              │
│   {action: "slow-sql/kill-lock-session",      │              │                                              │
│    params: {...}}                             │              │                                              │
│   │                                           │              │                                              │
│   ▼ gate.check(L2) → 处置授权判定（G3）          │              │                                              │
│   ①干跑 handler（注入的执行器接缝）──────────────┼────────────►│ runbook 解析 → 渲染命令清单+影响面 →          │
│      ToolResult{ok, dry_run 预览+proposal_id}  │              │   生成 proposal(pending) 落库（G8）          │
│   ▼                                           │              │                                              │
│ 模型收束 conclusion → 调查终态落库（M4 三表）      │              │   人工（第二道闸门，API 层）                   │
│                                              │              │   POST /remediations/{id}/confirm             │
│                                              │◄─────────────┤   {decision: approve|reject, reason?}         │
│  调查报告含处置节（proposal 状态+预览，G8）        │              │   ▼ approve                                   │
│                                              │              │   ③受控执行：逐条命令过白名单（G4）→            │
│                                              │              │     demo 容器内执行（禁 shell 拼串，R4）→留痕    │
│                                              │              │   ④恢复验证：runbook verification PromQL 回查（G6）│
│                                              │              │     恢复 → incident 翻 mitigated，proposal 收尾  │
│                                              │              │     未恢复 → 自动执行 runbook rollback（G7）     │
│                                              │              │       → 仍失败 → 转人工（D-28：不是丢弃）        │
└──────────────────────────────────────────────┘              └──────────────────────────────────────────────┘
```

**关键设计支点（推荐主线，G1/G3 评审确认）**：

1. **推理与执行分离**（架构 §1 总原则）：模型只做「请求处置」的决策（选 runbook/动作/参数）；确认后的命令清单**由系统锁定**——执行时永不回读模型输出、不把已批准清单交模型改写（防「批准后被偷换命令」）。这是把 Claude Code「deny 规则由 Claude Code 强制而非模型」的教训落进本项目：批准的对象是**干跑渲染出的具体命令清单**（proposal.dry_run_json），不是模型的意图描述。
2. **确认不阻塞调查**：确认门等人工是任意时长（分钟级），而调查会话同步运行且有 15 步 / 5min 硬闸（D-28）——因此 execute_action 在调查内**只产出干跑提案**，Planner 据此收束；确认/执行/验证发生在调查收尾后，由 remediation 服务独立驱动（挂在 incident 上）。loop.py 不改主循环结构（Loop 只编排不判断，架构 §3.2）。
3. **C3 依赖方向**：`oncall.remediation` 已在 pyproject C3 禁列（harness 不得 import）——execute_action 实装不 import remediation，handler 沿用 registry 现成的**执行函数注入接缝**（照 query_metrics handler + Fetcher 先例：组装点 app.py 把 remediation 执行器注入 registry，类型仅 TYPE_CHECKING/Protocol）；依赖方向 remediation → db（落库）、api → remediation（端点编排）均合法。

### 设计的模块

| 模块 | 动作 | 职责 | 目录 | 关联里程碑 |
|---|---|---|---|---|
| runbook 文档库 | 新增 | 2 个处置 runbook（`cpu-spike` / `slow-sql`，G9 选型）Markdown 源文件，内容与 `chaos/scenarios/*/cleanup.sh` 处置语义 + golden `expected_remediation` 对齐（G5 契约） | 仓根 `remediation/runbooks/`（与 chaos/ 平行，版本控制=R3 维护纪律；不进 src 包） | M5 → M6 历史处置知识化 |
| runbook 解析器 | 新增 | Markdown → `Runbook` 数据类（frontmatter slug/alert_ref/severity/actions/rollback/verification；正文为处置说明）；契约校验失败即拒绝加载（脏 runbook 进不了执行面） | `src/oncall/remediation/runbook.py`（预算 ≈180 行） | M5 |
| 处置状态机 | 新增 | proposal 状态流转（G2 状态集）+ 干跑渲染 + 确认/拒绝 + 恢复验证编排；无 LLM、确定性 | `src/oncall/remediation/service.py`（预算 ≈260 行） | M5 |
| 命令白名单 | 新增 | **系统层唯一可执行面**：原子操作表（动作类型 → 受限命令模板 + 参数白名单正则）；runbook action 只可引用白名单内原子操作，命令字符串永不来自自由文本（R4） | `src/oncall/remediation/allowlist.py`（预算 ≈130 行） | M5 |
| 受控执行器 | 新增 | demo 容器内执行（subprocess 列表参数、无 shell、30s 超时、输出限长）；仅接受白名单校验后的命令；执行留痕 | `src/oncall/remediation/executor.py`（预算 ≈150 行） | M5 |
| 恢复验证器 | 新增 | runbook verification PromQL 回查判恢复（复用 `context/promql` Fetcher 接缝注入）；恢复/未恢复机械判定 | `src/oncall/remediation/verifier.py`（预算 ≈120 行） | M5 → M7 处置成功率矩阵 |
| 确认门 API | 新增 | `POST /remediations/{id}/confirm|reject`（API 层拦截）+ `GET /remediations/{id}` 处置查询；确认/拒绝带 reason 留痕 | `src/oncall/api/remediation.py`（新文件，预算 ≈140 行） | M5 → M8 UI |
| execute_action 实装 | 改造 | stub → 干跑模式 handler：解析 action → 调注入的 remediation 执行器生成 proposal → 返回干跑预览；缺省未注入时维持 error stub 语义（既有测试零回退） | `src/oncall/harness/tools/registry.py`（handler 注入面 + `src/oncall/harness/tools/execute.py` 新文件） | M5 |
| PermissionGate 语义 | 改造 | L2「永远禁止」→ 按 G3 定案的条件放行（处置授权判定注入，`check()` 保持纯函数） | `src/oncall/harness/permission.py` | M5 |
| 处置落库 | 新增 | proposal 表（第八表，G8 显式偏差须评审）；处置节点与 evidence_steps/investigations 衔接 | `src/oncall/db/models.py`（现 ≈200 行，预算 ≤300）+ 接缝随 service | M5 → M6/M8 |

### C3/C6 论证

1. **harness → remediation 零静态依赖**：pyproject C3 禁列已含 `oncall.remediation`（import-linter 2026 年首版即预留，见 pyproject [tool.importlinter.contracts]「C3: harness 独立于业务模块」）。execute_action 实装分两层：harness 侧只定义「执行器接缝 Protocol」（干跑/授权判定接口，住 `harness/tools/execute.py` 或 permission.py 注入点），remediation 实现该接缝，`ingest/app.py create_app` 组装时经 registry 既有 handlers 注入参数传入——与 query_metrics/真实 Fetcher 注入（registry.py docstring「真实数据源 issue 03 才接」）同构，import-linter 静态面不新增 harness→remediation 边。
2. **api → remediation / remediation → db 单向合法**：api 层新增端点 import remediation（与 api→classify→db 同构）；remediation import `oncall.db`（第八表落库）合法；`context/promql` 不在 C3 禁列，verifier 复用其 Fetcher 接缝（注入）。
3. **C6 行数预算**：新文件行数预算见模块表（最大 service.py ≈260 < 300）；`models.py` 现 ≈200 行，第八表增 ≈45 行 → ≈245 行 < 300，暂不拆文件（若评审要求拆分 models 见风险 6）；loop.py（299 行）**不动主循环结构**——干跑提案在 handler 侧完成，loop 零净增（G1 推荐主线的 C6 收益）。
4. **A6 禁 shell 拼串**：受控执行器是仓库内第一个真实 subprocess 外呼面——一律 `subprocess.run([...], shell=False)` 列表参数 + 命令来自白名单模板非自由文本 + 参数过白名单正则（R4），bandit `S` 系列 + 架构守卫测试双重看管（见风险 3）。

### 数据模型变更

| 变更项 | 类型 | 说明 | 迁移方式 |
|---|---|---|---|
| `remediation_proposals` | **新增表（第八表，超架构 §4 七表清单，须评审 + 回写架构）** | 一次处置提案一行：`id, incident_id(FK), investigation_id(FK，nullable，产出调查), runbook_slug, action_id, status(G2 状态集), dry_run_json(干跑命令清单+影响面——批准即锁定, confirm/reject 用此渲染), params_json, decision, confirm_reason, confirmed_at, executed_at, verify_result_json, rollback_status, created_at, finished_at` | `create_all`（dev），Alembic 延至 MySQL |
| `incidents` | **状态流转（不扩列不扩枚举）** | 恢复验证通过 → incident.status 翻 `mitigated`（架构 §4 既有枚举）；未恢复转人工 → 保持 `investigating`（D-28 转人工不是丢弃） | — |
| `evidence_steps` | **无变更**（冻结列照 D-25） | execute_action 干跑调用本身已是证据步（tool=execute_action）；确认/执行/验证节点留痕落 proposal 表（G8） | — |
| `investigations` | **无变更**（D-31 冻结） | 会话级字段不扩；处置从报告经 GET 出口附 proposal 链接（G8 定案后细） | — |

### API 变更

| API | 变更类型 | 请求/响应要点 | 影响调用方 |
|---|---|---|---|
| `POST /remediations/{proposal_id}/confirm` | 新增 | **第二道闸门（API 层拦截）**：body `{decision: "approve"\|"reject", reason?: str}`；approve 触发受控执行 + 恢复验证（同步完成返回终态，或只翻 approved——G2）；reject 提案落 rejected 留痕；不存在/已终态 → 4xx | M8 UI / 人工 curl |
| `GET /remediations/{proposal_id}` | 新增 | 处置查询：干跑预览/状态/确认理由/执行输出摘要/验证结果/回滚状态——无 UI 阶段的处置留痕载体 | M8 时间线 |
| `GET /remediations?incident_id=` | 新增 | 按事件查处置列表（一事件可能多次处置尝试） | M8 / 人工 |
| `POST /investigate` | 语义增强（形状不变） | execute_action 干跑产出的 proposal 随调查收尾落库，报告 JSON 附 `remediation` 节（proposal 状态 + 干跑预览摘要，G8 定形状）；escalated 报告同出口 | 不变（M8 消费新节） |

## 验收标准

> 可实测、可判定；实测后回填打勾，不得虚构。设计期 mock-only（处置管线无 LLM，mock 决策脚本驱动 Planner），真实 demo 端到端留 T8（环境门槛：活 demo 栈，开工前需用户确认）。

- [ ] **2 剧本端到端自动处置恢复**（PRD §7-M5 硬口径）：`cpu-spike` + `slow-sql`（G9 选型）在活 demo 栈注入故障 → mock 决策调查 → execute_action 干跑 → 人工 confirm → 受控执行 → 恢复验证通过 → incident 翻 mitigated——真实 e2e 断言（T8，demo 栈就绪门槛）
- [ ] **写操作 100% 过确认门**（PRD §4「安全处置」）：全测试内无任何 execute_action 直接执行路径——所有写命令必经「干跑渲染 → pending → 人工 approve → 白名单校验 → 执行」；确认前系统不产生任何 demo 侧副作用（mock e2e 机械断言，T7）
- [ ] **干跑不落地**（第 1 门）：execute_action 调用后 demo 侧状态零变化（指标/进程/容器快照比对），ToolResult 返回命令清单+影响面+proposal_id（单测机械断言）
- [ ] **恢复验证判恢复/未恢复**（第 4 门）：runbook verification PromQL 回查——恢复路径 proposal 收尾 `recovered` + incident 翻 mitigated；未恢复路径自动执行 rollback 后仍失败 → 转人工（proposal 落 `escalated`、incident 保持 investigating，D-28）——mock 验证器断言两条路径
- [ ] **全程留痕**（PRD「过程可信」延伸）：proposal 行含干跑 JSON（= 被批准的命令清单）、确认 decision/reason/时间、执行输出摘要、验证结果、回滚状态；执行器的每条白名单命令均落审计（含校验前后参数）——单测断言
- [ ] **命令白名单拒绝越权**（第 3 门 + 硬规 3）：白名单外命令/参数（如任意 docker rm、shell 元字符注入样本）在执行器层被拒并留审计，runbook 引用不存在动作 → 拒绝加载——单测断言
- [ ] **D-23 冻结面不破**：六工具集合、`ExecuteActionInput` 形状、`ToolResult` 形状、loop 主循环结构不变（import + 键集合断言）；`EvidenceStep`/`Hypothesis`/`InvestigationSession` 契约不倒改（D-25）
- [ ] **全量门禁只增不减**：pytest（基线 541 passed / 10 skipped 只增不减）+ ruff check + ruff format --check + import-linter C3–C6 + A6 bandit 全绿，coverage ≥80%（harness 单独 ≥85%）
- [ ] **真实 e2e 回填**（T8）：2 剧本处置耗时/执行命令数/恢复窗口如实回填验收节与 issue Comments（禁虚构）；设计文档翻 `implemented`、D-39+ 落位核对、架构 §4 回写预告核对

## 依赖

- **前置依赖**：M4 8/8 resolved ✅（三表 + GET 读库 + `investigations.opening_card_json`）；D-23（execute_action L2 stub 接缝）/ D-28（终止与转人工语义）/ D-16/D-17（卡片与 context）/ D-31（investigations 覆盖语义）只消费不推翻；M3 `register_six_tools` handlers 注入面
- **数据 / 环境依赖**：SQLite dev 库 `oncall.db`；`datasets/golden/dev/`（mock 决策剧本夹具，holdout/ 禁读）；**T8 真实 e2e 需活 demo 栈**（docker compose 已起 + Prometheus 可查）——无 LLM key 门槛（处置管线零 LLM，mock 决策不花 key）
- **后续影响**：处置数据是 M6 知识库（历史处置召回）、M7 评测（处置成功率/恢复时间列）、M8 UI（确认门 + 处置时间线）的直接输入；`remediation_proposals` 表 + 命令白名单是「写操作自动化」叙事的工程底座，也是面试安全问答的实物

## 开放设计点（评审 grill）

> G1–G10 交用户逐条拍板（用户保留推翻权，推翻须回退对应设计节重议）；定案后登记 decisions.md D-39 起 + 拆票。外部标准来源见节末「评审依据」R1–R5（URL + 取用日期 2026-09-09）。

| # | 开放点 | 推荐默认解 | 理由 | 依据 |
|---|---|---|---|---|
| G1 | 处置编排位置：四道闸门全在调查循环内（execute_action 一步到位执行）vs 循环内干跑 + 收尾后独立流程执行 | **循环内干跑、确认后独立流程执行**：execute_action 实装为干跑模式（解析 runbook → 渲染命令清单+影响面 → 建 pending 提案），Planner 据此收束；确认/执行/验证由 remediation 服务在调查收尾后确定性驱动 | 确认门等人工是任意时长，调查同步运行且有 15 步/5min 硬闸（D-28）——循环内阻塞等确认会让 POST /investigate 挂起失控；批准对象锁定为干跑渲染出的**具体命令清单**，执行阶段不读模型输出——防「批准后模型偷换命令」（R1：权限由系统强制而非模型；R2：逐动作人工批准有确认疲劳）；loop.py 零净增保住 C6 | D-23/D-28/C3 + R1 + R2 |
| G2 | 确认门会话语义：confirm 即执行（同步等执行+验证完返回）vs confirm 只翻 approved + 独立 execute 触发；提案等待期超时语义 | **confirm 即执行**（approve 同步走受控执行 + 恢复验证，≤5min 预算内返回终态）；**提案不做自动过期**——pending 期 incident 保持 investigating，人工可随时 confirm/reject；转人工出口照 D-28（超 5min 不熔断提案本身，处置无步数概念，预算只在单步执行 30s） | 确认即执行 = 一步 API 完成「人点头 → 机器干完 → 回查」最小闭环（demo 处置动作秒级 + 恢复验证窗口 ≤60s）；分开 execute 端点只是把确认与执行拆两次调用，徒增「确认了但没人执行」中间态；提案不自动过期——自动作废一个人类尚未回复的处置请求违反「转人工不是丢弃」（D-28），pending 即最长久的可查状态 | D-28 + R1（permission prompt 无强制超时，人决定何时回） |
| G3 | PermissionGate L2「永远禁止」语义变更：放行条件放哪（gate 纯函数加授权判定 vs handler 前置校验）；`ExecuteActionInput` 是否需扩展 | **gate.check 保持纯函数**（L0/L1/L2 层级判定不变），L2 放行条件 = 注入的「处置授权判定器」：判定依据 = 该 incident+action 是否存在 **approved** proposal（进程内对账表/DB 查询，G8 定案后取表）；execute_action 每调一次先走干跑（无 approved 提案 → 干跑建提案返回预览；有 approved 提案 → 授权器放行走执行）——**实际本设计默认执行不走工具**（G1 主线），故 L2 在循环内仍以干跑产出为终；`ExecuteActionInput{action, params}` 不扩列，action = `runbook_slug/action_id`（如 `slow-sql/kill-lock-session`） | 安全面变更必须显式评审；推理与权限分离（架构 §1）要求判定逻辑不进 Planner/handler 决策路径——授权器是独立代码路径；approved 提案在循环内放行会让「模型再次执行已批准命令」成为可能（坏），故推荐循环内 L2 只出干跑（G1 一致）；action 语义 = runbook 定位器，参数 schema 由 runbook 定义校验——不扩 ExecuteActionInput 保 D-23 冻结面 | D-23/架构 §1/§3.4 + R1 |
| G4 | 命令白名单登记方式：静态原子操作表 + runbook 引用 vs runbook 内嵌自由命令逐条审核 | **静态原子操作表**：白名单 = 动作类型 → 受限命令模板 + 参数白名单正则（如 `container.remove(name=[a-z0-9_-]{1,64})`、`mysql.kill_session(container, session_id=int)`）；runbook 的 action 只能引用表内原子操作，命令字符串永不来自 runbook 正文或模型自由文本 | R4：命令必须对白名单校验、参数用白名单正则、禁 shell 拼接；R1：作用域化白名单（`Bash(rm *)`）而非裸工具放行——本项目对等物 = 动作级白名单而非「execute_action 全放行」；runbook 内嵌自由命令 = 把审核责任摊给每次内容变更，且命令来自文档字符串有注入面（A6） | 硬规 3 + A6/C4 + R1 + R4 |
| G5 | runbook 格式契约：字段集与解析落点（新模块 vs harness 扩展）；Markdown 解析依赖 | **格式契约（候选，评审微调）**：frontmatter YAML 六字段 `slug / alert_ref / severity / actions[] / rollback[] / verification`；actions[i] = `{id, name, steps:[引用白名单原子操作+参数模板]}`；rollback = 反向原子操作序列；verification = `{promql, condition, window_s}`；正文为处置说明（进模型上下文用）。解析落 `src/oncall/remediation/runbook.py`（新模块，C3 禁列已预留）；frontmatter 切分自写（≈30 行）不引 PyYAML/新依赖 | R3：playbook 必备 severity/impact/debug 建议/缓解与完全解决动作——本契约把「缓解动作」收敛为白名单引用的 actions、「完全解决」收敛为 verification；解析落 remediation 模块 = 与 C3「harness 不得 import remediation」同向（runbook 数据被 remediation 消费、经注入进工具层）；零新依赖（C2） | C2/C3 + R3 |
| G6 | 恢复验证判据来源：runbook verification 显式声明 vs 从锚定告警解析 vs 调查结论推导 | **runbook verification 显式声明**（promql + condition + window）；初始 2 runbook 的阈值取剧本实测回落值（cpu-spike：非 DB 路径 P95 ≤0.05s；slow-sql：`demo_db_pool_used` 回落至池上限内 + 锚定告警 resolved 观察窗 60s） | R3「fully resolve the alert」——解决 = 回到告警未触发的稳态，runbook 作者（人）在无压力时写清判据（R3：playbook 减少应激下即兴发挥）；从告警解析/调查推导都是运行时猜测，伪权威（D-18 教训：标注错=评测全错的对偶：判据错=恢复误判）；显式声明让恢复验证可机械断言 | D-18 教训 + R3 |
| G7 | 回滚动作来源：runbook 显式定义 rollback vs 系统自动推导（逆操作） | **runbook 显式定义 rollback**（frontmatter rollback 字段引用白名单原子操作序列）；系统不做逆操作推导 | 逆操作推导是伪权威（CPU 剧本回滚 = 恢复 cpuset + 停容器，不是「逆向 stress-ng」一条命令；slow-sql 回滚 = KILL 会话幂等重试）；显式定义 = 处置作者预写失败预案（chaos cleanup.sh 已是现成素材）；回滚同样过白名单 + 留痕 | chaos/scenarios cleanup 先例 + R3 |
| G8 | 处置留痕落点：第八表 `remediation_proposals` vs 复用三表（evidence_steps 特殊步 + investigations 扩列） | **第八表 `remediation_proposals`**（字段见数据模型节）；report 的 remediation 节读表序列化（照 D-35 形状定案先例） | proposal 生命周期跨调查（pending 等确认发生在调查收尾后），evidence_steps 锚调查步、提案锚 incident——语义不同不混表；investigations 扩列 = 推 D-31 会话级冻结面（第八表显式偏差走评审 + 回写架构 §4，M4 先例 G2/D-31）；evidence_steps 冻结列不动（D-25） | 架构 §4 + D-25/D-31 + M4 G2 先例 |
| G9 | 2 剧本选型 | **cpu-spike + slow-sql**（处置动作 = cleanup.sh 语义：docker 资源写操作 + mysql KILL 会话写操作，覆盖两类写面；恢复判据可机械判定：P95 ≤0.05s / 连接池回落；false-positive-flap 是误报剧本不处置、protocol-mismatch 处置动作复杂且恢复判定跨版本回滚成本高） | 恢复可机械判定是「恢复验证可机械断言」验收的前提；两类写面让命令白名单覆盖 docker + mysql 两个原子操作族（白名单通用性叙事）；与 M2/M3/M4 剧本复用最多（slow-sql 三 M 复用） | D-29 剧本先例 + chaos cleanup 语义 + golden dev |
| G10 | 降级形态（裁剪②：自动执行降为「干跑 + 建议」）是否作为显式 G 选项 | **接受为可裁剪达成路径，不作为默认范围**：默认范围含受控执行；裁剪达成 = 不注入受控执行器（confirm 端点落「建议已确认，执行能力未配置」503/说明 + proposal 留 approved 态即终）——设计保证该降级只需砍注入面，不改契约 | roadmap 裁剪②是时间不够的兜底叙事——设计须使降级形态可裁剪达成（票面要求）但默认不主动砍（PRD §7-M5 验收含「自动处置恢复」）；「干跑+建议」降级 = 承认系统仍给出可审计的处置建议（干跑即建议）而把执行留给人工 | roadmap 裁剪② + PRD §7-M5 |

### 评审依据（官方标准来源）

> 取用日期均为 2026-09-09。仓内契约（decisions D-16/17/19/22/23/25/28/30-38、架构 §1/§3.2/§3.3/§3.4/§4/§5/§7、CONTEXT.md、conventions/security-guardrails.md + quality-gates.md C2/C3/C4/C6/A6、chaos/scenarios/01-cpu-spike + 02-slow-sql cleanup.sh、golden dev 剧本、pyproject C3 禁列）是全部 G 点「只消费不推翻」的输入；外部来源仅四处安全/可靠性方法论引用 + 一处 runbook 定义。

| # | 来源 | 用于 |
|---|---|---|
| R1 | Claude Code Docs《Configure permissions》：权限规则由系统强制而非模型、deny→ask→allow 求值、作用域化规则 `Bash(rm *)` 而非裸工具放行：`https://code.claude.com/docs/en/permissions` | G1（批准对象锁定具体命令）/ G3（L2 放行独立代码路径）/ G4（动作级白名单先例） |
| R2 | Anthropic Engineering《How we contain Claude across products》：人工逐动作批准存在确认疲劳（telemetry 93% 批准率使监督失焦），containment/环境边界优于逐动作监督：`https://anthropic.com/engineering/how-we-contain-claude` | G1（循环内只干跑、能系统层锁定的不逐动作问）/ G2（确认即执行的疲劳最小化） |
| R3 | Google SRE Workbook《On-Call》：playbook 必备 severity/impact/debug 建议/缓解与完全解决动作、每告警应有对应 playbook 条目、playbook 随生产变更腐烂需维护、确定性命令清单推荐自动化、系统不能自采取的动作才分页给人：`https://sre.google/workbook/on-call/` | G5（runbook 契约字段）/ G6（fully resolve + 预写判据）/ runbook 库版本控制纪律 |
| R4 | OWASP《OS Command Injection Defense Cheat Sheet》：命令必须对白名单校验、参数白名单正则、禁 shell 拼接、参数化 API：`https://cheatsheetseries.owasp.org/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.html` | G4（白名单形态）/ 受控执行器安全面 / A6 落地 |
| R5 | 仓内契约（非外部来源）：decisions.md D-16/17/19/22/23/25/28/30-38 + 架构 §1/§3.2/§3.3/§3.4/§4/§5/§7 + security-guardrails.md（四道闸门/白名单/操作分级）+ quality-gates.md（C3 harness 禁 import remediation/C4/C6/A6）+ chaos cleanup 脚本 + CONTEXT.md 术语 | 全部 G 点 |

## 开发计划（任务拆解）

> 节奏：W5 前半（M5 处置 + M6 知识库并行窗口的 M5 侧）；目标 2–3 天（每日 1–2h）。TDD 红绿循环照硬规 12：runbook 契约校验、白名单判定、proposal 状态机、恢复判据是约定接缝。
> 就绪态判据（照 M3/M4 先例）：**G1–G10 定案后 T1–T8 转 `ready-for-agent`**（T8 附环境门槛：需活 demo 栈，开工前需用户确认；零 LLM key——处置管线不经 LLM）。拆票建 `.scratch/m5-remediation-gates/` 归评审后动作（见节末）。

**关键里程碑**：

- **M5-A 干跑与提案内核**（T1–T3）：runbook 契约 + 解析器 + execute_action 干跑实装 + proposal 落库与状态机
- **M5-B 确认门与受控执行**（T4–T5）：confirm API + 命令白名单 + demo 执行器
- **M5-C 恢复验证与验收**（T6–T8）：恢复验证/回滚 + 验收断言与门禁 + 真实 e2e 收尾

| # | 任务 | 内容 | 验收（机械判定，实测回填） | 依赖 | 就绪态 |
|---|---|---|---|---|---|
| T1 | runbook 契约 + 解析器 | `remediation/runbooks/` 2 runbook 源文件（G5 契约 + G9 选型，内容与 cleanup.sh/expected_remediation 对齐）；`remediation/runbook.py` frontmatter 解析与契约校验（零新依赖）；runbook 库加载面 | pytest 绿：2 runbook 解析通过、字段缺省/非法引用/白名单外动作拒绝加载断言 | G5/G9 | ready-for-agent |
| T2 | execute_action 干跑实装 | registry 注入面接 remediation 执行器（Protocol，TYPE_CHECKING）；干跑 handler 解析 action → 渲染命令清单+影响面 → 建 pending proposal → 返回预览；未注入维持 stub 语义 | pytest 绿：干跑返回 ok+预览+proposal_id、demo 侧零副作用（快照比对）、未注入路径既有测试零回退、ToolResult 形状不变 | G1/G3/T1 | ready-for-agent |
| T3 | proposal 落库与状态机 | `remediation_proposals` 表（G8 形状）+ service 状态流转（pending→approved/rejected→executing→recovered/failed/rolled_back/escalated） | pytest 绿：状态机非法迁移拒绝、proposal 行字段全落、incident 关联正确 | G8/T2 | ready-for-agent |
| T4 | 确认门 API | `POST /remediations/{id}/confirm|reject`（G2：approve 同步执行）/ `GET` 处置查询；确认 reason 留痕 | pytest 绿：confirm/reject 端点契约、批准后触发执行断言（mock 执行器）、reason 落库、4xx 语义 | G2/T3 | ready-for-agent |
| T5 | 命令白名单 + 受控执行器 | allowlist（动作类型→模板+参数正则）+ executor（demo 容器 subprocess 列表参数、30s 超时、禁 shell）；执行审计 | pytest 绿：白名单内命令放行、越权/注入样本拒绝留审计、执行器超时/输出限长、A6 bandit S 扫描绿 | G4/T3 | ready-for-agent |
| T6 | 恢复验证 + 回滚 | verifier（runbook verification PromQL 回查，Fetcher 注入）+ 未恢复自动 rollback + 仍失败转人工（D-28） | pytest 绿：恢复路径 proposal 收尾 recovered + incident mitigated；未恢复→rollback→escalated + incident 保持 investigating；verifier 注入替身断言 | G6/G7/T5 | ready-for-agent |
| T7 | 验收断言与门禁 | 「验收标准」节逐条转机械断言（100% 过确认门/干跑不落地/留痕/白名单拒绝/冻结面不破）；mock e2e 2 剧本决策脚本驱动；架构守卫全绿 | pytest 绿：验收节断言全绿；全量门禁基线 541/10 只增不减 + ruff 双检 + import-linter + bandit 全绿 | T1–T6 | ready-for-agent |
| T8 | 真实 e2e 与收尾 | 活 demo 栈 2 剧本端到端自动处置恢复（**开工前需用户确认 demo 栈就绪**；mock 决策脚本零 LLM key）；耗时/命令数/恢复窗口回填 → 翻 implemented；CONTEXT 新术语；D-39+ 落位核对；架构 §4 第八表回写（G8 定案时预告） | 实测回填完整（禁虚构）；2 剧本处置恢复断言绿；全量 pytest + ruff 双检绿 | T7 | ready-for-agent（demo 栈就绪门槛） |

## 风险清单（评审随附，交用户复核后才可派工实现票）

| # | 风险 | 现状与对策 |
|---|---|---|
| 1 | demo 容器写操作的真实副作用 | 受控执行器仅 demo 容器内 + 白名单动作族收窄（docker/mysql 两类）+ 干跑预览先行 + 确认门人审——demo 是自建可重建环境（D-01 活环境），最坏情况 = 重建 demo 栈，blast radius 封顶（R2 containment 思路） |
| 2 | 确认疲劳（真实轮人工确认） | 本设计只 2 剧本 × 每剧本 1 次确认，无疲劳面；R2 引用的 93% 批准率教训已转化为「能系统层锁定的不逐动作问」（白名单原子操作不需要逐次人工确认，只有 runbook 动作级处置需要一次确认） |
| 3 | 执行器是首个真实 subprocess 外呼面 | 禁 shell=True 拼串 + 白名单模板 + 参数正则 + 30s 超时 + bandit S 系列/AST 守卫测试；命令源 = 白名单原子操作（非文档正文/模型自由文本），注入面从源头收口（R4） |
| 4 | 干跑渲染与执行一致性漂移（渲染说一套、执行做另一套） | 批准对象 = proposal.dry_run_json 具体命令清单；执行器只消费该清单（解析为白名单原子操作序列），不存在「二次解释」路径 |
| 5 | 恢复验证误判（判据错 = 恢复误报） | 判据来自 runbook verification 显式声明（G6），阈值取剧本实测回落值；验证结果落 verify_result_json 供 M7 复盘 |
| 6 | models.py 行数逼近 C6（现 ≈200 + 第八表 ≈45） | ≈245 < 300 暂可容纳；若评审要求拆分，import-linter 同包（models 子模块 `models_remediation.py`）无新边，实现票内过评审后执行 |
| 7 | execute_action 注入接缝破坏既有测试 | 未注入时维持原 stub 语义（error 兜底）——既有 mock 测试零回退；新增干跑路径只走显式注入（T2 断言） |
| 8 | 处置超时拖慢 confirm API | confirm 同步执行预算：执行 ≤30s/步 × 动作步数 + 验证窗口 ≤60s，2 剧本动作 ≤3 步 → 单次 confirm ≤ ~2.5min < 5min 总预算；超时走转人工出口不悬挂 |

## 评审后动作（G 表定案后执行，本票不预落盘）

1. G 表定案说明回填 + 本文件翻 `reviewed`（评审人/日期回填）
2. `docs/design/decisions.md` 登记 **D-39+**（G1→D-39 起逐条；先 grep 确认无占用；逐条过 ADR 三判据，表格行格式照现有表续行）
3. `CONTEXT.md` 新术语入表（处置提案 / 命令白名单 / 干跑预览 等，含 `_Avoid_`）
4. `.scratch/m5-remediation-gates/` 拆票：`spec.md`（任务序列表 + M5/M6/M7 边界节）+ `issues/01–08` 与 T1–T8 一一对应；验收可机械判定的标 `ready-for-agent`，涉 G 取舍的按定案说明；T8 附 demo 栈门槛注明
5. 架构文档回写随实现票执行并预告：§4 七表 → 八表（G8 定案时）、§3.3 权限分级 L2 语义注记（G3）、§5 时序「匹配 SOP → 干跑 → 人工确认(M5)」兑现核对（G1）
6. `docs/README.md` 索引行随草案已新增（本文件条目）
