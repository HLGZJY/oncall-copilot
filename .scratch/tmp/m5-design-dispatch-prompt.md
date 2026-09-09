# M5 设计文档派工 prompt：处置与恢复验证（四道闸门）设计 + 评审 + 拆票

oncall-copilot M5 开工票：产出 `docs/design/m5-remediation-gates-design.md`
设计文档（照 M4 形制），围绕「runbook 解析 → 干跑 → 人工确认 API → 受控
执行 → 恢复验证/回滚」四道闸门展开；G 开放点交用户逐条评审定案后才拆票。
**设计期零写码、零建表、零真实 LLM 调用**（照 M4 设计期口径先例）。

## 0. 开工门槛

- git 基线：`git log --oneline -1` 确认 HEAD ≥ `ca4302e`（M4-08 收官提交），
  工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- 设计期无 key/预算门槛（零真实调用）；若任务拆解出真实调用票，票面注明
  「开工前需用户确认 key」照 M3/M4 issue 08 流程，本设计会话不执行

## 1. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）
- Python 解释器（venv，勿用系统 python）：
  `C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- M4 收官基线：**541 passed / 10 skipped / coverage 97.88%** + ruff 双检 +
  架构守卫全绿（10 skipped 含 3 个花钱开关真实 e2e + 7 常规 skip）
- pytest 统计行：pyproject addopts 已含 `-q`，命令行勿再传 `-q`；统计行核对
  用重定向 + 退出码

## 2. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航——本票设计的宪法级
   输入是硬规 3「安全护栏落系统层、白名单不用黑名单」+ 硬规 4「写操作四道
   闸门缺一不可」）
2. `CONTEXT.md`（术语权威——已有词直接用：四道闸门 / 干跑 / 人工确认门 /
   恢复验证 / Runbook SOP / 白名单 / 三道防线；新概念当场入表）
3. `docs/prd.md` §7-M5（M5 权威范围：runbook Markdown 解析为工具；干跑 →
   确认 API → 执行 → 回查指标验证恢复 → 未恢复自动回滚或转人工；**验收 =
   2 个剧本端到端自动处置恢复、全程留痕、写操作全部经过确认门**）+ §4 处置
   相关口径
4. `docs/plans/roadmap-m0-m9.md`（M5 = W4 后半–W5 前半；裁剪方案②「M5 自动
   执行降为干跑+建议」是兜底叙事——设计须使该降级形态可裁剪达成，但不主动砍）
5. `docs/architecture/architecture.md`（§3.2/§3.3 权限分级 L0/L1/L2 + 主流程
   「匹配 SOP → 干跑 → 人工确认(M5)」落位；§4 七表现状——若 M5 需新表/扩列
   属显式偏差须评审 + 回写预告）
6. `src/oncall/harness/tools/registry.py` + `src/oncall/harness/permission.py`
   （现状：`execute_action` 为 D-23 六工具之一的 **L2 stub**，PermissionGate
   永远禁止；M5 是**实装**不是加工具，不改 D-23 冻结面）+
   `src/oncall/harness/loop.py`（主循环编排边界：Loop 只编排不判断）
7. `docs/design/decisions.md`（D-16/D-17/D-19/D-23/D-28 只消费不推翻；最新
   编号 **D-38**，本阶段新决策自 **D-39** 起登记）+
   `docs/design/m4-evidence-chain-design.md`（**设计文档形制模板**：目标/
   技术类别/技术方案/数据模型变更/API 变更/验收标准/依赖/开放设计点 G 表/
   评审依据 R 表/风险清单/任务拆解 T 表/评审后动作）
8. `docs/design/feature-design-template.md`（设计文档骨架模板）
9. 母本回溯（只在提炼版语焉不详时查）：`docs/reference/_sources/项目信息.md`
   模块 D（处置自动化设计）+ §6.3（四道闸门 / 白名单细节）
10. `docs/conventions/`（工程纪律权威）+ `docs/agents/issue-tracker.md` 与
    `docs/agents/triage-labels.md`（拆票约定）

## 3. 任务（设计三段式：起草 → 评审 → 拆票）

1. **起草设计文档** `docs/design/m5-remediation-gates-design.md`（status:
   `draft`），至少覆盖：
   - **runbook 解析**：Markdown runbook 的格式契约（动作/回滚/前置条件字段）、
     解析落点（新模块 vs `harness/` 扩展，过 C3/C6 论证）、2 个候选剧本的
     runbook 内容来源（与 `chaos/` 注入脚本 + `datasets/golden/dev/` 剧本对齐，
     holdout/ 禁读）
   - **四道闸门实装**：干跑（打印命令+影响面）→ 人工确认门（**API 层拦截**：
     确认端点契约、确认态留痕、超时语义）→ 受控执行（execute_action 从 stub
     实装，命令白名单机制、L2 PermissionGate 放行条件变更）→ 恢复验证（回查
     指标判恢复、未恢复自动回滚或转人工，终止语义对齐 D-28）
   - **证据链衔接**：处置步如何进 `evidence_steps`/报告（M4 落库链路复用，
     D-33/D-34 口径）——全程留痕验收的落点
   - **安全面**：命令白名单（照硬规 3 系统层护栏）、审计留痕、demo 容器内
     受控执行的外呼面声明（真实面边界照 M3/M4 先例写清）
   - **数据模型/API 变更表** + **验收标准**（逐条可机械判定，禁虚构口径）+
     **风险清单** + **任务拆解 T 表**（含依赖与就绪态判据）
2. **开放设计点 G 表**：每条带「开放点 / 推荐默认解 / 理由 / 依据」；依据含
   官方标准来源（URL + 取用日期，照 M4 R1–R5 形制）+ 仓内契约引用；**评审前
   不拆票**。预期 G 点方向（供参考，不预设结论）：确认门超时与会话语义 /
   白名单登记方式 / 恢复验证的指标判据与阈值来源 / 回滚动作来源（runbook
   定义 vs 自动推导）/ 处置留痕落库位置（复用三表 vs 新表 = 第八表显式偏差）/
   2 剧本选型 / 降级形态（干跑+建议）是否作为 G 选项
3. **交用户评审**：G 表逐条请用户拍板（用户保留推翻权，推翻须回退对应设计
   节重议）；定案后执行评审后动作——
   - 文档翻 `reviewed`（评审人/日期回填）
   - `decisions.md` 登记 **D-39+**（逐条过 ADR 三判据，表格行格式照现有表续行）
   - `CONTEXT.md` 新术语入表（含 `_Avoid_`）
   - `docs/README.md` 索引行新增
   - 拆票 `.scratch/m5-remediation-gates/`：`spec.md`（任务序列表 + 里程碑
     边界节：M5/M6/M7 边界写清）+ `issues/01–0N` 与 T 表一一对应；**验收可
     机械判定的才标 `ready-for-agent`，涉架构取舍的一律 `ready-for-human`**；
     真实调用票附 key 门槛注明
   - 架构文档回写（如需）随实现票执行并预告（照 M4 评审后动作第 5 条形制）

## 4. 边界（勿越）

- **设计期零写码**：不改 `src/`、`tests/`、`chaos/`、`datasets/`；不动既有
  冻结契约（D-17/19/22/23/28 只消费；incidents 五字段不扩列）
- 评审定案前**不建 issue 目录、不登记 decisions**（先 G 表定案再落盘，防
  悬空决策）
- `datasets/golden/holdout/` 禁读；母本 `_sources/` 只回溯不引用
- 事实性外部引用（官方标准/框架文档）须检索核实并记 URL + 取用日期，禁凭
  记忆写依据
- 提交规范：中文 + type 前缀（设计文档预期 `docs(M5-处置闸门): ...`，拆票
  可并入同笔或拆 `chore`/`docs` 票，body 写**为什么**）；引用路径用本地
  tracker 文件路径；不跳过 hooks；高危 git 命令按硬规 11 先报后动

## 5. 已知踩坑

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ② pytest `-qq` 吞统计行；统计行用重定向 + 退出码核对
- ③ `execute_action` 是 D-23 六工具之一——M5 是**实装既有 stub**，勿表述成
  「新增第 7 工具」；PermissionGate L2「永远禁止」语义的变更属安全面变更，
  必须进 G 表评审
- ④ 架构 §4 现为七表（M4 已回写 `investigations`）；M5 若需新表是显式偏差，
  走评审 + 回写预告，勿直接写进架构文档
- ⑤ M4 真实实测遗留：Top-1 0/3 与同参绕圈留 M7 复核口径——M5 设计不受其
  阻塞，但调查循环质量假设不要押在 flash 档位命中率上
- ⑥ 最新决策编号 D-38，新决策从 D-39 起；登记前 grep 确认无占用
- ⑦ 权威票面/设计文档行数预算 C6 ≤300 行约束的是 `src/` 生产文件，设计
  文档不受限，但新生产模块的行数预算须在设计中论证

## 6. 验证路径（收尾清单）

- [ ] git 基线确认 ≥ `ca4302e`、工作区干净
- [ ] 设计文档落 `docs/design/m5-remediation-gates-design.md`，覆盖 §3.1 全部
      要素（runbook 解析/四道闸门/证据链衔接/安全面/数据模型/API/验收/风险/T 表）
- [ ] G 表逐条带推荐解 + 理由 + 依据（含官方来源 URL + 取用日期），交用户评审
- [ ] 用户定案后：翻 `reviewed` + decisions D-39+ 登记 + CONTEXT 新术语 +
      docs/README 索引 + 拆票（spec + issues，就绪态判据合规）
- [ ] 评审前后各一次全量门禁确认（pytest 541/10 只增不减 + ruff 双检绿），
      证明设计期确实零写码
- [ ] 收尾汇报：G 定案表 + 拆票清单（含就绪态与阻塞边）+ 前沿（下一个可派
      issue 编号）+ 遗留风险
