# M4-07 派工 prompt：验收断言与门禁（T7）

oncall-copilot M4 第 7 票：把设计文档「验收标准」节逐条转**机械断言**收口——
本票是**断言汇总票**，01–06 各票已带的单测在此逐条核对映射，缺失项补齐、
不可机械判定的条目停手上报。**不写新功能；严格 TDD；零 LLM 真实调用、零 HTTP 外呼。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **06 落位后的 HEAD**（开工前 `git log --oneline -1` 确认，预期为 `e580aae` feat(M4-证据链) schema 摘要提交），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**以 06 落位后实测为准，只增不减**）：**537 passed / 7 skipped / coverage 98%**（pyproject 门禁线 ≥80%，实测 98%）——票面与设计文档写的 479 是 04 时代旧口径，**以本条为准勿回改设计文档**（实测上涨属正常）；ruff check + ruff format --check 全绿
- pytest 统计行：pyproject addopts 已含 `-q`，**命令行勿再传 `-q`**（`-qq` 会吞统计行）；统计行走 stderr/管道时用重定向 + 退出码取
- 容器栈不需要起；不需要任何 LLM API key；测试一律 mock harness 组件 + 内存 SQLite（**勿写坏仓库根现库 `oncall.db`**）；`datasets/golden/holdout/` 禁读

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票复用：证据步 / 假设 / 调查会话 / 证据链；**无新术语**）
3. `.scratch/m4-evidence-chain/issues/07-acceptance-assertions.md`（**权威票面**，下方为摘要）+ `.scratch/m4-evidence-chain/spec.md`「任务序列表」与验收相关行
4. `docs/design/m4-evidence-chain-design.md`——重点：**「验收标准」节 9 条**（本票映射对象，逐条转断言）、§开发计划 T7 行
5. `docs/design/decisions.md`：**D-35**（导出 JSON 形状权威 = M3 `build_report` 现形状，键集合契约测试精确守卫，照 D-17 `test_alert_card_api.py` 先例）、**D-37**（opening 键集合断言）、**D-38**（schema 摘要 + ≤1500 tokens 断言，06 已落位）、D-25（M3/M4 边界只消费不推翻）、D-28（escalated/aborted 已取证部分可查）
6. 架构权威：`docs/architecture/agent-loop-design.md` §证据链数据形状（按 D-35 已对齐冻结契约）+ §4 冻结列名（`docs/architecture/` 下 storage 相关文档）
7. 既有测试盘点（开工前先 grep 盘点，列「已有断言 ↔ 验收条目」映射，再补缺）：
   - `tests/e2e/` + `tests/integration/`（mock 3 剧本 `cpu-spike` / `slow-sql` / `queue-backlog` 走 `POST /investigate` 的端到端先例）
   - `tests/test_architecture_guards.py`（C3/C4/C5/C6/A2 守卫现状）
   - `tests/unit/`：issue 01–06 各票落位的单测（三表 ORM / repo 写接缝 / report 端点 / markdown 导出 / opening 视图 / schema 摘要）
8. `docs/conventions/`（工程纪律权威）

## 2. 任务（权威票面 = issue 07，下方为摘要）

落位：`tests/`（新增收口断言测试 + 逐条映射清单落 issue 07 Comments 节）；**生产代码预期零改动**（发现必须改生产代码才能让断言变绿 = 验收条目未兑现，停手上报勿硬凑）。

「验收标准」节 9 条映射口径（★ = 本票收口核对，☆ = T8 范围不在本票）：

- ★ **100% 落库**：mock 3 剧本经 `POST /investigate` 走至 conclusion 后，`evidence_steps` 行数 = 会话步数、`hypotheses` 行数 = 假设池大小、`investigations` 一行且终态一致（三表断言，01/02 已有单测的收口核对）
- ★ **任意一步可回溯**：落库后任一 `evidence_steps.output_json` 与该步 `ToolResult`（status/data/meta）逐字段一致；escalated/aborted 会话已取证部分可查（D-28）
- ★ **导出 JSON 形状**：与 agent-loop-design 数据形状比对一致（D-35 口径 = M3 `build_report` 现形状，键集合契约测试精确守卫，照 `test_alert_card_api.py` 先例；03 落位后应已有，核对即可）
- ★ **Markdown 导出可用**：`report.md` 端点 200 + `text/markdown` + 含全部步/假设/结论/终态（04 已有，核对）
- ★ **M3/M4 边界不破**：`EvidenceStep` / `Hypothesis` / `InvestigationSession` 契约字段与 M3 一致（import + 键集合断言），指针接口不变（D-25）
- ★ **缺口① opening 视图**：`_decide_with_retry` view 含 `opening` 键（D-37 键集合断言）+ C6 断言全绿（05 已有，核对）
- ★ **缺口② schema 可见性**：system prompt 含六工具 schema 摘要 + ≤1500 tokens 断言不破（06 已有 4 条测试，核对）
- ☆ **重跑真实实测**：T8/issue 08 范围（需用户确认 key），**本票不做**，映射清单标注「归 T8」即可
- ★ **全量门禁只增不减**：pytest + ruff 双检 + coverage ≥80%（基线见 §0）

TDD 顺序建议：先盘点出「验收条目 ↔ 既有测试」映射清单（红/绿状态一目了然）→ 缺失条目逐条补测试（红→绿）→ 映射清单落 issue 07 Comments → 架构守卫核对 → 全量门禁。

## 3. 边界（勿越）

- **生产代码只读**（src/ 零 diff 是本票默认形态）；仅动 `tests/` + issue 07 文件 + spec.md 任务序列表 07 行
- **不许放宽断言凑绿**：发现验收条目无法机械判定（如口径含糊、依赖运行时人工观察）→ 停手，issue 07 翻 `ready-for-human` 并注明卡点，交用户复审
- 发现既有测试与验收条目**口径冲突**（如 479 旧基线 vs 537 实测）：以实测为准，测试不改语义只对齐口径
- ruff 照旧：`max-args=5`、函数 ≤50 语句、PLR0911 ≤6；新增测试文件走 `tests/unit/` 裸 import conftest 惯例
- 零 LLM 真实调用、零 HTTP 外呼；pytest autouse 断网 fixture 在位
- 提交规范：中文 + type 前缀，预期 `test(M4-证据链): ...`；body 写**为什么**（验收口径从文档承诺收敛为机械断言是 L3 证据链可信的底线；汇总票防「各票绿但整体不闭环」）；引用 `.scratch/m4-evidence-chain/issues/07-acceptance-assertions.md`；不跳过 hooks

## 4. 已知踩坑（M0–M4 实录，含本票针对性）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ② pytest `-qq` 吞统计行（06 实录）：统计行核对用重定向 + 退出码
- ③ tests/unit 不是包（裸 import conftest）；内存 SQLite 夹具照 01/02 先例，勿碰根目录 `oncall.db`
- ④ **键集合断言用精确守卫**（== 集合比较），勿用子串/包含——照 D-17 `test_alert_card_api.py` 先例
- ⑤ escalated/aborted 会话夹具照 D-28 终态语义造（escalate 是转人工不是丢弃），勿另造终止口径
- ⑥ 基线数字以本 prompt §0 实测为准（537/7/98%），票面 479 是旧口径——**回填 issue 07 注记时写明此差异**，不改设计文档历史行文
- ⑦ 映射清单是本票核心交付物之一：验收节 9 条 ×（对应测试名 / 新增 or 已有 / 状态）逐行落 issue Comments，缺一行即未收口

## 5. 验证路径（收尾清单）

- [ ] 逐条映射清单落 issue 07 Comments（9 条全覆盖，T8 条目标注「归 T8」）
- [ ] 缺失断言补齐且全绿（新增测试数如实汇报）
- [ ] `tests/test_architecture_guards.py` 全绿（C3/C4/C5/C6/A2）
- [ ] 门禁：pytest **537 passed / 7 skipped 只增不减** + coverage ≥80%（实测 98%）+ ruff check + ruff format --check 全绿
- [ ] issue 07 验收四条打勾 + `Status:` 翻 `resolved`；spec.md 任务序列表 07 行勾选
- [ ] 收尾汇报：映射清单全文 + 新增/核对测试清单 + 门禁计数对账（基线 vs 实测）+ **08 就绪确认**（08 被 05/06/07 阻塞，07 resolved 后 08 解除阻塞，但 08 开工前需用户确认 LLM key——汇报里显式提示该门槛）
