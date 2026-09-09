# M4 设计文档派工 prompt：证据链与过程存储（设计草案 + 开放点评审）

oncall-copilot 进入 M4「证据链与过程存储」（W4 前半，PRD §7-M4，**不可砍三支柱之一**）。本会话只做**设计文档**：产出 `docs/design/m4-evidence-chain-design.md` 草案（status: `draft`），照 M3 设计文档形制列出开放设计点 G1–Gn 交用户逐条评审拍板。**本票零写码、零建表、零真实调用**——评审定案后的「评审后动作 + 拆票」是下一份派工 prompt 的事，本会话在文档里预留该节即可。

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`86b80b0` 之后 T8-B 提交**（M3 8/8 resolved 收官），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（只读参考，本票不改码不应变动）：**479 passed / 7 skipped，coverage 98.05%** + ruff check + ruff format --check
- 本票**允许改动**：`docs/design/` 新增 1 份设计文档、必要时 `docs/README.md` 导航行；**不改任何 src/tests/datasets**；`.scratch/` 本票不建目录（拆票归评审后动作）
- 设计期口径：LLM 全 mock（照 M2/M3 先例），设计文档须写明真实调用留实现票、开工前需用户确认 key

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）——重点：硬规 5（证据链三必须）、硬规 7（评测纪律/失败模式归类）、硬规 10（实测回填禁虚构）
2. `CONTEXT.md`（术语权威——本票必用：调查会话 / 证据步 / 假设 / 调查收尾结构 / 调查报告 / 转人工；新术语（如落库后的表名、报告格式词）当场入表并写 `_Avoid_`）
3. `docs/prd.md` §7-M4（**权威需求**：每步 `{step, thought, tool, input_summary, output_summary, ts, cost}` 落库；事故-证据链一对多模型；导出 JSON/Markdown；验收 = 一次完整调查可导出报告、任意一步可回溯原始工具输出）+ §4 验收口径（证据链 100% 落库）
4. `docs/plans/roadmap-m0-m9.md` W4 行 + 裁剪约束（M4 不可砍）
5. `docs/architecture/architecture.md` §3.3（ContextManager/记忆）与证据链数据形状章节；`docs/architecture/agent-loop-design.md`（**报告 JSON 数据形状的比对权威**）
6. `docs/design/m3-investigation-loop-design.md`（**形制模板 + 契约来源**）：§目标第 4 条（**M3/M4 边界已定案：M3 定义 `EvidenceStep` / `Hypothesis` 内存契约与写入接口，M4 建表落库时契约不变，只把内存对象替换为行 id**）+ §技术方案 + §验收标准（M4 相关条目）+ 全文结构（status → 目标 → 技术类别 → 技术方案 → 验收标准 → 依赖 → 开放设计点 G → 开发计划 T → 风险清单 → 评审后动作）
7. `src/oncall/harness/session.py`（`InvestigationSession` / `EvidenceStep` / `Hypothesis` 契约现状——M4 落库的输入形状）+ `src/oncall/harness/loop.py`（步数/失败模式/终止语义 D-28）
8. `src/oncall/db/models.py`（M2 已有 `alert_events` / `incidents` 表，SQLAlchemy DeclarativeBase 先例；`incidents.id` 即事故-证据一对多的「一」端）+ `src/oncall/db/views.py`
9. `.scratch/m3-investigation-loop/issues/08-e2e-validation.md`（**M4 必须承接的输入**）：
   - **两个结构缺口**（harness 冻结停手注记，修复建议随 M4）：
     ① Planner 视图缺事件锚点——`loop._decide_with_retry` view 仅 `{system_prompt, steps, hypotheses, notices}`，接缝承诺 D-25「记忆摘要 + 事件锚点」未实装；修复建议 view 增 `opening`（D-17 卡片精简投影），注意 loop.py 已 297 行贴 C6
     ② 工具入参 schema 不可见——架构 §3.3 承诺 tool_help 通道，D-23 冻结六工具未含；修复**二选一需小评审**：system prompt 附六工具入参 schema 摘要，或补第 7 个 tool_help 工具（改 D-23 面）
   - **真实实测 Top-1 首版 0/3**（畸形率 ≈0%，主失败是参数级失败 + 同参绕圈）；两缺口修复后需**重跑真实实测**（真实调用开工前需用户确认 key）
   - M4 衔接口径：`InvestigationSession` 契约与 failure_mode/步数/成本字段即 **M7 评测矩阵列**
10. `docs/design/decisions.md` D-17（事件卡片）/ D-22（决策输出协议）/ D-23（六工具冻结面）/ D-25（视图口径）/ D-28（终止语义）/ D-29（判分）——M4 新决策从 **D-30** 起编
11. `docs/agents/issue-tracker.md` + `docs/agents/triage-labels.md`（评审后拆票要用，本票只预留）

## 2. 任务

### 主任务：`docs/design/m4-evidence-chain-design.md` 草案

照 M3 设计文档全文形制（frontmatter: title/summary/source/status=draft/updated/read_when），至少覆盖：

1. **目标 / Non-goals**：把 PRD §7-M4 三件事（逐步落库 / 事故-证据链模型 / 导出 JSON+Markdown 报告）展开为要解决的问题；明确 M3/M4 边界（内存契约不变，内存对象替换为行 id）；Non-goals 写明不做 M6 报告生成（时间线美化/改进建议）、不做 M7 评测 runner、不做 UI 页面
2. **技术方案**（开放点先行，不定案处标 G）：落库存储选型（SQLite 现库 `oncall.db` 加表 vs 新库；SQLAlchemy 先例延续）、表模型（证据步表 / 假设表 / 会话表与 `incidents` 外键关系；`input_json`/`output_json` 原始全文与 `input_summary`/`output_summary` 摘要双存的回溯口径）、`InvestigationSession` → 落库的写入时机（步进即写 vs 收尾批量写）、成本字段来源（`LLMUsage` 汇总落会话/步级）、导出报告格式（JSON 照 agent-loop-design 数据形状；Markdown 版式留 M6 或本票最小版）
3. **两缺口修复方案纳入范围**：缺口①（view 增 `opening`）与缺口②（schema 摘要 vs tool_help 工具）作为开放点列出交评审；修复后**重跑真实实测**列为 M4 实现票之一（key 门槛照 M3-08 先例写明）
4. **验收标准**（可机械判定，供拆票 `ready-for-agent`）：一次完整调查（mock 3 剧本即可）落库后任意一步可回溯原始工具输出；导出报告 JSON 形状与 agent-loop-design 比对一致；证据链 100% 落库断言；门禁只增不减
5. **依赖 / 开放设计点 G1–Gn / 开发计划 T1–Tn / 风险清单**：G 每条给出**推荐默认解 + 理由**（M3 先例：用户逐条拍板采纳推荐解）；T 拆票粒度参照 M3 的 T1–T8
6. **评审依据 R1–Rn**：涉及外部标准/官方文档的开放点（如 JSON 报告形状、审计可回溯性惯例），检索官方来源给 URL 与取用日期，交用户复核

### 评审会话（同会话内进行）

- 草案成文后，把 G1–Gn 整理成**逐条拍板问题清单**呈现用户（每条：问题 + 选项 + 推荐解 + 理由），等用户逐条定案
- 用户定案后：文档 status 翻 `reviewed`、开放点写定案结论、新决策登记 `decisions.md`（D-30 起）、新术语入 `CONTEXT.md`
- **拆票是评审后动作**：本会话若用户明确要求继续，才建 `.scratch/m4-evidence-chain/`（spec.md + issues/，标签约定照 issue-tracker.md）；否则在文档「评审后动作」节写明待办即可收尾

### TDD / 验证口径

- 本票零码，无 pytest 新增；收尾跑一遍全量门禁确认零回归（479/7/98.05% 不变）+ ruff 双检
- 设计文档自检：frontmatter 五字段齐、status 生命周期声明齐、每个 G 有推荐解、每条验收可机械判定、M3/M4 边界与 issue 08 注记逐字承接

## 3. 边界（勿越）

- 不改 `src/` / `tests/` / `datasets/` 任何文件；不建表不迁移（落库是实现票的事）
- `datasets/golden/holdout/` 禁读含 raw 切片
- 不预支 M6/M7/M8 范围（报告美化、评测 runner、UI）
- C6 ≤300 行是实现约束，设计文档在方案节须给新文件行数预算提示（loop.py 297 行贴上限——缺口①修复若动 loop.py 须写拆分预案）
- 文档分层纪律：设计文档落 `docs/design/`，frontmatter `source` 回指 PRD/架构/decisions 的具体章节；不新建根目录文档
- 提交规范：中文 + type 前缀，预期 `docs(M4-证据链): ...`；body 写为什么；不跳过 hooks

## 4. 已知踩坑（M0–M3 实录，设计期相关子集）

- ① 同文件多次编辑必须**串行**，下一回合 grep 复核落盘（M3-06/M3-07 两次实锤）
- ② decisions.md 是**表格行**格式（`| D-xx |`），新增决策照现有表续行，不另起 `## D-xx` 标题
- ③ M3 设计文档的 status 节有**状态机声明 + 当前状态 + 上一状态追溯**三段式，M4 照抄结构
- ④ 引用 issue 08 注记时**逐字承接**两个缺口的表述（这是评审 grill 的输入，转述走样会误导定案）
- ⑤ `docs/design/` 每份文档要有 `read_when`；`docs/README.md` 若有索引需同步加行
- ⑥ 设计期成本/步数等数字引用 M3 实测值（真实轮 ≈¥0.008/6 次、步数 2–5）时标注来源与日期，禁虚构新数字
- ⑦ ruff/format 不适用于纯文档，但 E501 习惯保留：文档正文行宽也尽量 ≤100，表格列多时允许超

## 5. 验证路径（收尾清单）

- [ ] `docs/design/m4-evidence-chain-design.md` 成文：frontmatter 齐 + status=draft + 全节形制对齐 M3 先例
- [ ] G1–Gn 拍板问题清单已呈现用户；用户定案后 status 翻 `reviewed`、decisions.md 登记 D-30+、CONTEXT.md 新术语入表
- [ ] 两结构缺口（issue 08 注记①②）均落入开放点/方案节，且「修复后重跑真实实测（key 门槛）」列为实现票
- [ ] M7 衔接写明：failure_mode/步数/成本字段即评测矩阵列
- [ ] 「评审后动作」节写明拆票待办（`.scratch/m4-evidence-chain/` spec + issues + 标签约定）
- [ ] 全量门禁零回归（479 passed / 7 skipped / 98.05%）+ ruff 双检；提交 1–2 笔（草案一笔、定案回填一笔），收尾汇报含 G 逐条定案表
