# m5-remediation-gates · Spec

M5 处置与恢复验证（四道闸门）（W4 后半–W5 前半，PRD §7-M5）。**权威设计**：`docs/design/m5-remediation-gates-design.md`（status: **reviewed**，2026-09-09 G1–G10 评审定案；评审依据见该文档 R1–R5）。

> **派工解冻**：G1–G10 已于 2026-09-09 全部评审定案（用户逐条拍板采纳推荐解，登记 `decisions.md` **D-39–D-48**，新术语入 `CONTEXT.md`）；T1–T8 就绪态终审完成，按编号顺序派工。T8 真实端到端需活 demo 栈（docker compose 已起 + Prometheus 可查），**开工前需用户确认 demo 栈就绪**（环境门槛非 key 门槛——处置管线零 LLM，mock 决策脚本不花 key）。

## 目标

把 D-23 冻结的 `execute_action` **L2 stub 实装**为四道闸门处置管线：runbook（Markdown）解析为可执行动作（第一道闸门前置输入）→ 干跑（打印命令+影响面，第 1 门）→ 人工确认门（API 层拦截，第 2 门）→ 受控执行（命令白名单 + demo 容器内，第 3 门）→ 恢复验证（回查指标，未恢复自动回滚或转人工，第 4 门）；处置全程留痕（proposal 第八表 + 执行审计）并并入证据链报告。**推理与执行分离**——模型只做「请求处置」决策，批准对象 = 干跑渲染的具体命令清单，执行阶段系统锁定、永不回读模型输出（D-39）。

## 关键契约（2026-09-09 评审定案，D-39–D-48；执行时不得擅改）

- **冻结输入只消费不推翻**：D-17（卡片 13 键）/ D-19（incidents 五字段不扩列不扩枚举——处置后翻 `mitigated` 属既有枚举值流转）/ D-22（Planner 协议）/ D-23（**六工具集合、`ExecuteActionInput{action, params}`、`ToolResult` 形状只消费不推翻**——M5 是实装既有 stub，不加第 7 工具）/ D-25/D-31（evidence_steps/investigations 冻结）/ D-28（终止与转人工语义——escalate 不是丢弃）
- **编排（D-39/G1）**：循环内干跑 + 收尾后独立流程执行——`execute_action` 实装为干跑模式（解析 runbook → 渲染命令清单+影响面 → 建 pending 提案返回预览），Planner 据此收束；确认/执行/恢复验证由 remediation 服务在调查收尾后确定性驱动；loop.py 主循环结构零净增（Loop 只编排不判断）
- **确认门（D-40/G2）**：confirm 即执行（approve 同步走受控执行+恢复验证，≤ ~2.5min 返回终态）；提案**不自动过期**——pending 期 incident 保持 investigating；超时走转人工出口（D-28）不熔断提案
- **权限（D-41/G3）**：`gate.check` 保持纯函数，L2 放行 = 注入的「处置授权判定器」（approved 提案存在性）；**循环内 L2 仍只出干跑**（批准后循环内放行 = 允许模型二次执行已批命令，坏）；授权判定是独立代码路径不进 Planner/handler 决策
- **白名单（D-42/G4）**：静态原子操作表（docker/mysql 两族：动作类型→受限命令模板+参数白名单正则）；runbook action 只可引用表内原子操作，命令字符串永不来自 runbook 正文或模型自由文本
- **runbook 契约（D-43/G5）**：frontmatter YAML 六字段 `slug / alert_ref / severity / actions[] / rollback[] / verification`；解析落 `src/oncall/remediation/runbook.py`（新模块，C3 禁列已预置；frontmatter 切分自写零新依赖）；契约校验失败即拒绝加载
- **恢复判据（D-44/G6）**：runbook verification **显式声明**（promql+condition+window），阈值取剧本实测回落值；从告警解析/调查推导是伪权威禁止
- **回滚（D-45/G7）**：runbook **显式定义 rollback**（frontmatter 引用白名单原子操作序列）；系统不做逆操作推导
- **留痕（D-46/G8）**：新增第八表 `remediation_proposals`（超架构 §4 七表规划显式偏差，随 T3 落库票回写架构 §4）；report remediation 节读表序列化
- **剧本（D-47/G9）**：2 剧本 = `cpu-spike` + `slow-sql`（处置 = chaos cleanup.sh 语义：docker 资源写 + mysql KILL 会话写）
- **降级（D-48/G10）**：可裁剪达成路径不作为默认范围——降级 = 不注入受控执行器（confirm 落 503 说明 + proposal 留 approved 即终），只砍注入面不改契约
- **架构守卫**：C3（harness 禁 import remediation——execute_action 实装走 registry 既有**执行函数注入接缝**（TYPE_CHECKING/Protocol，照 query_metrics + Fetcher 先例））/ C4/C5 / C6 ≤300 行（service.py ≈260 上限；models.py 现 ≈200 + 第八表 ≈45 → ≈245 暂不拆）/ A6（执行器禁 shell 拼串，列表参数 + 白名单正则，bandit S 系列守卫）
- **数据纪律**：只碰 `datasets/golden/dev/`（holdout/ 禁读）；runbook 内容与 `chaos/scenarios/01-cpu-spike + 02-slow-sql` cleanup.sh 处置语义 + golden `expected_remediation` 对齐；实测数字禁虚构
- **TDD + mock-only**：处置管线零 LLM（确定性系统行为），mock 决策脚本驱动 Planner；门禁基线 **541 passed / 10 skipped** 只增不减 + ruff 双检 + import-linter + bandit

## 任务序列

| Issue | 任务 | 对应设计文档 | Blocked by | 就绪态 |
|---|---|---|---|---|
| 01 | runbook 契约 + 解析器（runbook 库 2 源文件 + runbook.py 契约校验） | T1 / G5 / D-43 | — | resolved |
| 02 | execute_action 干跑实装（registry 注入面 + 干跑 handler + PermissionGate 授权判定器） | T2 / G1/G3 / D-39/D-41 | 01 | resolved |
| 03 | proposal 落库与状态机（第八表 + service 状态流转） | T3 / G8 / D-46 | 02 | resolved |
| 04 | 确认门 API（confirm/reject + GET 处置查询） | T4 / G2 / D-40 | 03 | resolved |
| 05 | 命令白名单 + 受控执行器（allowlist + executor + 执行审计） | T5 / G4 / D-42 | 03 | resolved |
| 06 | 恢复验证 + 回滚（verifier + 未恢复 rollback + 转人工） | T6 / G6/G7 / D-44/D-45 | 05 | ready-for-agent |
| 07 | 验收断言与门禁（验收标准节逐条转机械断言 + mock e2e 2 剧本） | T7 | 01–06 | ready-for-agent |
| 08 | 真实 e2e 与收尾（活 demo 栈 2 剧本端到端自动处置恢复 + 回填翻 implemented + 架构 §4 回写核对） | T8 | 07 | ready-for-agent（**demo 栈就绪门槛：开工前需用户确认**） |

## M5/M6/M7 边界（spec 级写清）

- **M6**：处置记录向量化入库、历史处置召回、报告内容生成（时间线美化/改进建议）——M5 只保证处置数据落库可取（proposal 表 + report remediation 节是 M6 的输入）
- **M7**：处置成功率/恢复时间列（proposal `verify_result_json`/`rollback_status` 是数据源）；`eval_runs` 评测行不混表——M5 不建评测设施
- **M8**：确认门 UI 交互（按钮/SSE）与处置时间线——M5 的确认门 = REST API + 无 UI 阶段最小形态（curl 可确认）；`GET /remediations` JSON 即无 UI 阶段载体
- **M5 内部里程碑**：M5-A 干跑与提案内核（01–03）→ M5-B 确认门与受控执行（04–05）→ M5-C 恢复验证与验收（06–08）
