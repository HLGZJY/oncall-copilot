# M5 issue 07 派工 prompt — 验收断言与门禁（T7，mock e2e 2 剧本收口）

> 新开会话执行本票。prompt 完整自洽：含环境、TDD、门禁、设计引用与验收标准，照 issue 01–06 惯例。
> 执行完把「断言文件分布、mock e2e 剧本驱动方式、设计文档验收节勾选数、验收计数实际值」列给用户确认后再入库。

## 0. 环境与仓库基线

- 仓库：`F:\Git repository/oncall-copilot`（Windows 11 / Git Bash；文件与解释器一律绝对路径）
- Python：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（装包走清华源；本票零新依赖）
- **基线 HEAD：`f7f4c26`**（前序：`0d7c504` issue 05 → `f7f4c26` issue 06）。开工先 `git status` 确认工作区干净（仅 `.workbuddy/` 日志与 `.scratch/tmp/` 派工 prompt 属例外，**勿动、勿入库**）。
- 前置依赖已满足：issue 01–06 全部 resolved（01 runbook 解析器 → 02 execute_action 干跑 + L2 授权判定 → 03 proposal 第八表 + 状态机 → 04 确认门 API → 05 白名单 + 受控执行器 → 06 恢复验证 + 回滚编排）。
- **门禁基线实测：`667 passed / 10 skipped`**（issue 06 入库后真值）。⚠️ 票面写的「基线 541/10」是 M3 末期旧值，以 667/10 为准；实现后按实际数字更新票面该行。
- 硬规：AGENTS.md 12 条 + commit 规范（中文 + type 前缀、body 写为什么、Closes 用 issue 文件路径）；术语一律用 CONTEXT.md 词；TDD 红绿循环；零 LLM、零真实外呼（golden 替身 + MockPlanner + 替身执行器/验证器，pytest-socket 全局断网；**活栈真实验证归 issue 08，本票严格 mock**）。

## 1. 必读（权威源，按顺序）

1. `.scratch/m5-remediation-gates/issues/07-acceptance-assertions.md` —— **本票权威**，验收 4 条逐条打勾。
2. `docs/design/m5-remediation-gates-design.md`「**验收标准**」节（约 127–139 行）—— 逐条转机械断言的对象；注意其中「真实 e2e 回填（T8）」条**不在本票**（归 08）。「D-23 冻结面不破」条照现有 import + 键集合断言风格。
3. `docs/design/decisions.md` **D-39–D-48**（涉 G 取舍的断言按定案说明写，不得引入未评审语义）+ **D-25**（EvidenceStep/Hypothesis 冻结）/ **D-28**（escalated 不是丢弃）。
4. **M4-T7 先例（本票形制模板）**：`tests/e2e/test_m4_acceptance_persistence.py` —— 断言汇总票的落点形制：各 issue 单测已覆盖部件，此处收口「跨 issue 端到端」断言；以及 `tests/e2e/test_e2e_cpu_spike.py` / `test_e2e_slow_sql.py`（mock 决策驱动 + golden 替身先例）。
5. 代码落点（实现前必读）：
   - `src/oncall/harness/tools/execute.py` + `src/oncall/harness/tools/registry.py`（issue 02：干跑 handler + 注入接缝，ToolResult 形状）
   - `src/oncall/harness/permission.py`（issue 02：L2 处置授权判定器，gate.check 纯函数）
   - `src/oncall/remediation/service.py`（issue 03：状态机 + `_TRANSITIONS` + dry_run_json 不可变）
   - `src/oncall/api/remediation.py`（issue 04/06：`RemediationDeps{executor, verifier, runbooks}`、D-48 降级 503、confirm 链单调用点）
   - `src/oncall/remediation/allowlist.py` / `executor.py`（issue 05：`ATOMIC_ACTIONS`、`ControlledExecutor` 审计形状 `{executed, rejected, ok, output_summary}`）
   - `src/oncall/remediation/verifier.py`（issue 06：`RunbookRecoveryVerifier` verify 返回形状 `{recovered, promql, condition, window_s, observed, samples}` + error fail-closed；`run_confirm_chain` 分叉；rollback_status 三值 skipped/rolled_back/blocked）
   - `remediation/runbooks/cpu-spike.md` / `slow-sql.md`（issue 01 定稿：rollback=[] 直边 / $session_id runtime_values 注入）
   - `tests/unit/test_remediation_*.py` / `test_execute_action_dryrun.py`（既有覆盖盘点：**先盘点再写断言，已覆盖的引用不重写，只补跨 issue 缺口**）
   - `datasets/golden/dev/cpu-spike.yaml` / `slow-sql.yaml`（golden `expected_remediation` 对账源；`datasets/golden/holdout/` 禁读不变）
6. 踩坑预铺：C8 模块级常量 `UPPER: Final[...]`；同文件多次 Edit 串行 + grep 复核；A1 测试禁 import httpx/requests/openai 等 SDK（替身全用普通类）；**架构守卫 test_files_are_short ≤300 行/文件**——e2e 断言文件超限时按主题拆两个文件，不放宽守卫；fixture 断网由 conftest 提供。

## 2. 任务（交付物）

### A. 验收标准节逐条机械断言（落 `tests/e2e/test_m5_acceptance_gates.py`，照 M4-T7 形制）

对设计文档「验收标准」节逐条收口，**先盘点 issue 01–06 既有单测已覆盖项**（引用注释标注归属即可），本票补跨 issue 缺口：

- **写操作 100% 过确认门**：端到端断言「无 approve 提案 → execute_action 只产干跑（ToolResult ok + 预览 + proposal_id，demo 侧零副作用）」「未经 approve 的 proposal 不可能进入执行面」（状态机 `_TRANSITIONS` 键集合断言 approved 无执行出边 + api 只经 confirm 触达 run_confirm_chain）
- **干跑不落地**：execute_action 调用前后 demo 侧快照比对（替身执行器零调用断言）+ ToolResult 三键（命令清单/影响面/proposal_id）形状断言
- **恢复验证判恢复/未恢复**（第 4 门）：issue 06 已覆盖两条路径，此处补**跨层收口**：api confirm → run_confirm_chain → incident 翻转全链一条龙断言（恢复→mitigated / 未恢复→escalated+investigating，D-28）
- **全程留痕**：一条走完 confirm 链的 proposal 行逐字段对账——dry_run_json 原文（deep-equal，D-39 不可变）、decision/confirm_reason/confirmed_at、params_json["execution"]（issue 05 审计形状）、verify_result_json（issue 06 形状）、rollback_status、finished_at
- **命令白名单拒绝越权**：注入样本（`;&|` 元字符、白名单外动作、多余参数）→ 执行器层 rejected 留审计；runbook 引用不存在动作 → 拒绝加载（issue 01/05 已覆盖，收口断言即可）
- **D-23 冻结面不破**：六工具集合 import + 键集合断言、`ExecuteActionInput{action, params}`、`ToolResult` 形状、loop.py 主循环结构（行数/结构断言照 M4 先例）；`EvidenceStep`/`Hypothesis`/`InvestigationSession` 列契约不倒改（D-25）

### B. mock e2e 2 剧本（落 `tests/e2e/test_m5_mock_e2e.py`）

- `cpu-spike` + `slow-sql` 各一条全链路：golden dev 剧本驱动 MockPlanner（零 LLM）→ 调查收束 execute_action 干跑建 pending proposal → `POST /remediations/{id}/confirm approve`（替身执行器 + 真实 runbook 库）→ 恢复验证（Fetcher 替身返回判据满足快照）→ proposal `recovered` + incident `mitigated` 断言
- slow-sql 补未恢复变体：Fetcher 替身返回判据不满足 → rollback（`$session_id` 经 runtime_values 注入）→ 复验仍失败 → `escalated` + incident 保持 investigating（D-28）
- golden `expected_remediation` 与 runbook action/rollback 对账断言（2 剧本 × 字段级）
- 零真实 HTTP / 零真实 sleep（观察窗快照替身，真实窗口归 08）

### C. 回写（随本票同一个 feature commit）

- issue 07 票：`Status: ready-for-agent → resolved`、验收 4 条打勾、落位注记回填（断言文件分布、mock e2e 剧本脚本位置、**基线数字修正为实际**）
- 设计文档「验收标准」节：**mock 可判定条目**勾选 `- [x]` 并注记实测（「2 剧本端到端自动处置恢复」「真实 e2e 回填」两条 T8 项**不勾**，注明归 08；禁虚构实测数据）
- `.scratch/m5-remediation-gates/spec.md`：07 行就绪态 → `resolved`
- CONTEXT.md：预计无新词（断言收口不产新概念）；若真出现共享词汇当场入表（硬规 12）
- decisions.md：不新增 D；如实现中发现契约缺漏 → 停手问用户走评审

## 3. 边界（明确不做）

- ❌ 不接真实 Prometheus / 不真实 sleep / 不做 create_app 真实 executor/verifier 装配（全部归 issue 08；T8 开工前另有 demo 栈就绪门槛需用户确认）
- ❌ 不做 M8 UI、不做提案过期（D-40）、不改 runbook 源与白名单表
- ❌ 不改 issue 01–06 已定稿的语义（verify 形状 / rollback_status 取值 / 状态机迁移表 / D-48 降级）；只消费
- ❌ 零 LLM / 零真实 HTTP 外呼 / 零新依赖；`datasets/golden/holdout/` 禁读
- ❌ 不把 `.workbuddy/`、`.scratch/tmp/` 卷进 commit

## 4. 已知裁决与踩坑（预铺，实现时留痕）

1. **断言收口 ≠ 重写**：M4-T7 先例的核心纪律——issue 01–06 单测已覆盖的部件断言不搬家不重写，注释标注归属；本票只补「跨 issue 端到端」与「验收节逐条」缺口。
2. **T8 两条不勾**：设计文档验收节「2 剧本端到端自动处置恢复（T8）」「真实 e2e 回填（T8）」依赖活 demo 栈，本票 mock-only 不碰、不勾选。
3. **冻结面断言要硬**：D-23 冻结面用 import + 键集合精确断言（M4 先例），不用「存在性」软断言；loop.py 零净增是 G1 的 C6 收益，结构断言防回归。
4. **基线数字**：票面「541/10」是 M3 末期旧值；实测当前 667/10。实现后按实际（667 + 本票新增）更新票面该行。
5. **C6/C8/A1 踩坑**：新测试文件 ≤300 行（超限按主题拆分）；模块级常量 `UPPER: Final[...]`；同文件多次编辑串行 + grep 复核；替身普通类不 import SDK。
6. **提交粒度**：一个 feature commit（`test(M5-处置闸门): ...` 或 `feat(M5-处置闸门): ...`，以断言为主可用 `test`，scope 与 issue 01–06 一致）含断言 + 票面 + 设计文档回写；body 写为什么；`Closes .scratch/m5-remediation-gates/issues/07-acceptance-assertions.md`。不跳过 hooks。

## 5. 验证清单（门禁，逐条过）

- [ ] TDD：先写断言跑红（对未收口项）→ 实现补齐跑绿；纯断言票以「既有全绿 + 新断言全绿」为绿
- [ ] 全量 `python -m pytest tests`：`667 passed / 10 skipped` → 只增不回退
- [ ] `ruff check .` 与 `ruff format --check .` 全绿
- [ ] 架构守卫全绿（文件 ≤300、remediation 零 import harness、无 shell=True、C8）
- [ ] import-linter C3–C6 全绿 + A6 bandit 绿（pyproject 现行 skips 不放宽）
- [ ] 设计文档验收节 mock 可判定条目勾选 + 实测注记（T8 两条不勾）
- [ ] issue 07 验收 4 条逐条打勾
- [ ] 收尾汇报给用户确认后入库：断言文件分布、mock e2e 驱动方式、勾选数、验收计数实际值
