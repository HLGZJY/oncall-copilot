# M4-06 派工 prompt：缺口② 工具 schema 摘要进 system prompt（T6 / G9；D-38）

oncall-copilot M4 第 6 票：修复 M3 issue 08 注记②——工具入参 schema 不可见
（query_metrics 必填 start/end 全靠盲猜，参数失败后 notice 循环消耗步数）。
评审定案**选 A：schema 摘要**（D-38）——system prompt 附六工具入参 schema
摘要，**不补第 7 个 tool_help 工具**（不改 D-23 冻结面）。**严格 TDD；零 LLM
真实调用、零 HTTP 外呼。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **05 落位后的 HEAD**（开工前 `git log --oneline -1` 确认，预期为 M4-05 opening 视图提交；**06 与 05 同碰 `context_manager.py` 且预算断言含 opening 后形态，必须串行、以 05 落位为基线**），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests --tb=no -p no:warnings -q`（05 落位后计数以其实测为准，当前口径 **523+ / 7 skipped，coverage ≥98%**）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest 统计行在 stderr（`-q` 模式）——重定向 + 退出码取（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key；测试一律 mock harness 组件（照 T6 先例）+ 内存 SQLite（勿写坏仓库根的现库 `oncall.db` 文件）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票复用：工具注册表 / Tool Registry、六工具冻结面 / D-23；**无新术语**，若确需造词当场入表并写 `_Avoid_`）
3. `.scratch/m4-evidence-chain/issues/06-tool-schema-gap2.md`（**权威票面**，下方为摘要）+ `.scratch/m4-evidence-chain/spec.md`「关键契约」节
4. `docs/design/m4-evidence-chain-design.md`——重点：§技术方案 T6 行、开放点 G9 定案（= D-38）、§开发计划 T6 行
5. `docs/design/decisions.md`：**D-38**（system prompt 附六工具入参 schema 摘要——静态模板 A2 落位，每工具 2–3 行，预算 +≈250 tokens 且 ≤1500 断言钉死；不补 tool_help；架构 §3.3「tool_help 通道」按意图由 schema 摘要兑现，措辞随票回写）；冻结面 **D-23**（六工具冻结不扩）、**D-38 不动 A2 的 MAX_INLINE_PROMPT=200**（模板落模块级常量即合规）
6. M3 issue 08 注记②原文（已逐字引在票面 blockquote，理解动机用）
7. 架构权威：`docs/architecture/agent-loop-design.md` §3.3——「tool_help 查详情」措辞所在（**本票回写一行注记，见任务④**）
8. 代码（行号以 05 落位后为准，开工前现场核对）：
   - `src/oncall/harness/context_manager.py`——`build_system_prompt`（本票主改点）、`SYSTEM_PROMPT_TOKEN_LIMIT = 1500`（预算断言复用它）、`SUMMARY_TEMPLATE` / `SYSTEM_ROLE` 模块级常量先例、`estimate_tokens` 估算口径
   - `src/oncall/harness/tools/registry.py`——六工具注册与**参数契约**（schema 摘要必须与之同源/逐字对齐，防文档与运行时校验漂移；重点：query_metrics 的 `{promql, start, end, step?}`、search_logs 的 `limit≤100`）
   - `src/oncall/harness/tools/schemas.py`——ToolResult / 参数 schema 定义
   - `tests/unit/test_harness_loop.py`（mock e2e 3 剧本回归锚）+ `tests/unit/test_context_manager.py`（system prompt 单测先例，若有）
9. `docs/conventions/`（工程纪律权威）

## 2. 任务（权威票面 = issue 06，下方为摘要）

落位：`harness/context_manager.py`（schema 摘要模板常量 + build_system_prompt 拼装）+ `harness/tools/registry.py` 或 `schemas.py`（仅当需导出参数契约供对齐时才动，能不动则不动）+ 架构文档一行注记 + 测试新增。

- **schema 摘要模板**：模块级常量（大写命名，照 SUMMARY_TEMPLATE 先例，A2 合规），每工具 2–3 行（工具名 + 必填/可选参数 + 约束值域）；`build_system_prompt` 拼装进现有 prompt
- **同源约束（本票核心质量线）**：摘要与 ToolRegistry 实际参数校验同源——从注册契约派生（首选）或写**逐字对齐测试**（registry 参数契约 ↔ 摘要内容），漂移即红
- **预算控制**：摘要估 +≈250 tokens；`SYSTEM_PROMPT_TOKEN_LIMIT=1500` 断言**随本票钉死**（用 `estimate_tokens` 实测完整 prompt 断言 ≤1500）；超限先压缩一句话描述，**不砍 schema 本身**（它是缺口②的主修复）
- **架构 §3.3 措辞注记回写**：一行说明「tool_help 通道按意图由 D-38 schema 摘要兑现」，交用户复核（`docs/architecture/agent-loop-design.md` 一行 diff，不改其它措辞）
- **不补第 7 个 tool_help 工具**（不改 D-23 冻结面、不动 `register_six_tools` 六工具集合）
- 既有 mock e2e 3/3 零回退（MockPlanner 不消费 system prompt 语义，但预算与 prompt 形状变化不得破坏断言）

TDD 顺序建议：schema 摘要与 registry 契约逐字对齐单测（红→绿）→ build_system_prompt 含六工具摘要 + tokens ≤1500 断言 → mock e2e 3/3 回放零回退 → 架构注记落盘 → 全量门禁。

## 3. 边界（勿越）

- 生产代码只动：`harness/context_manager.py`（模板常量 + 拼装）；`harness/tools/registry.py` / `schemas.py` 仅在需要导出契约时最小改动，**不改运行时校验行为**；`docs/architecture/agent-loop-design.md` 仅一行注记
- **不改**：`harness/loop.py`、`harness/planner.py`、D-23 六工具集合、`estimate_tokens` 估算口径、1500 上限数值（只钉断言不调预算）
- ruff：函数 ≤50 语句、PLR0911 出口 ≤6、`max-args=5`；C6 `context_manager.py` ≤300 行（05 落位后约 197 行，预算充足仍须核对）；A2 模板常量落模块级
- 零 LLM 真实调用、零 HTTP 外呼；`datasets/golden/holdout/` 禁读
- 提交规范：中文 + type 前缀，预期 `feat(M4-证据链): ...`；body 写**为什么**（schema 可见直击参数级失败主模式 + 同源约束防漂移 + 选 A 不补 tool_help 的理由）；引用 `.scratch/m4-evidence-chain/issues/06-tool-schema-gap2.md`；不跳过 hooks

## 4. 已知踩坑（M0–M4 实录，含本票针对性）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ② ruff：`max-args=5`、PLR0912 ≤12、PLR0913 ≤5、函数 ≤50 语句、PLR0911 ≤6
- ③ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture
- ④ **tokens 估算口径勿自造**：断言必须用 `estimate_tokens` 现行实现（测试里 estimator 可注入），别用 len//4 之类土法另立口径
- ⑤ **schema 摘要与校验漂移是本票最大风险**：registry 的必填/可选与值域以 `register_six_tools` 注册的契约为准，写对齐测试时从契约数据结构取值，勿手抄字符串后不管
- ⑥ pytest 统计行在 stderr（`-q` 模式）：重定向 + 退出码取
- ⑦ 架构注记只一行 diff——超出一行即越权，措辞拿不准就先停下发用户复核

## 5. 验证路径（收尾清单）

- [ ] issue 06 验收五条逐项打勾（system prompt 含六工具 schema 且与 registry 契约一致 / tokens ≤1500 断言 / mock e2e 3/3 / 架构 §3.3 一行注记落盘 / 全量门禁），在 issue 文件内回填注记
- [ ] 架构守卫全绿：`tests/test_architecture_guards.py`（重点 A2）
- [ ] 门禁：pytest（05 落位后基线只增不减，coverage ≥98%）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；`.scratch/m4-evidence-chain/spec.md` 任务序列表 06 行勾选
- [ ] 收尾汇报：落位文件清单 + schema 摘要样例全文（交用户复核）+ tokens 实测值（拼装前后对账）+ 架构注记 diff + 07 就绪确认（07 被 05/06 阻塞，两票 resolved 后即可派）
