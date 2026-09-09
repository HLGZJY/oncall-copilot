# M5 issue 06 派工 prompt — 恢复验证 + 回滚（T6 / G6 / G7 / D-44 / D-45 / D-28）

> 新开会话执行本票。prompt 完整自洽：含环境、TDD、门禁、设计引用与验收标准，照 issue 01–05 惯例。
> 执行完把「verifier 判定形状、rollback_status 取值、回滚分叉编排落点、验收计数实际值」列给用户确认后再入库。

## 0. 环境与仓库基线

- 仓库：`F:\Git repository/oncall-copilot`（Windows 11 / Git Bash；文件与解释器一律绝对路径）
- Python：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（装包走清华源；本票零新依赖）
- **基线 HEAD：`0d7c504`**（前序：`58ce4e8` issue 04 → `0d7c504` issue 05）。开工先 `git status` 确认工作区干净（仅 `.workbuddy/` 日志与 `.scratch/tmp/` 派工 prompt 属例外，**勿动、勿入库**）。
- 前置依赖已满足：issue 05（命令白名单 + 受控执行器 `ControlledExecutor`，含 `runtime_values` 注入与审计返回形状）**已入库** → `Blocked by: 05` 解除。
- **门禁基线实测：`649 passed / 10 skipped`**（issue 05 入库后真值）。⚠️ 票面验收第 6 条写的「基线 541/10」是 M3 末期旧值，以 649/10 为准；实现后按实际数字更新票面该行。
- 硬规：AGENTS.md 12 条 + commit 规范（中文 + type 前缀、body 写为什么、Closes 用 issue 文件路径）；术语一律用 CONTEXT.md 词（恢复判据/恢复验证/转人工/回滚均已入表）；TDD 红绿循环；零 LLM、零真实外呼（pytest-socket 已全局断网；PromQL 回查用 Fetcher 注入替身，活栈真实验证留 issue 08）。

## 1. 必读（权威源，按顺序）

1. `.scratch/m5-remediation-gates/issues/06-recovery-verification-rollback.md` —— **本票权威**，验收 6 条逐条打勾。
2. `docs/design/m5-remediation-gates-design.md`：
   - **§G6 行（约 158 行）**：恢复判据来源 = runbook verification 显式声明（promql + condition + window_s），不从锚定告警解析、不从调查结论推导（R3：判据错 = 恢复误判，D-18 教训的对偶）
   - **§G7 行（约 159 行）**：回滚动作来源 = runbook 显式定义 rollback，**系统不做逆操作推导**；回滚同样过白名单 + 留痕
   - 模块表 verifier 行（预算 ≈120 行）+ §C3/C6 论证 + 风险表「验证窗口/回滚编排」相关行
3. `docs/design/decisions.md`：**D-44**（恢复验证判据）/ **D-45**（回滚来源，rollback=[] 合法）/ **D-28**（转人工不是丢弃：escalated 落点、incident 保持 investigating）/ **D-40**（confirm 即执行同步链，api 层一行不重写状态逻辑）。**本票不新增 D 编号**；实现中发现契约缺漏（如 api 层分叉改动的归属）→ 停手问用户走评审，不自行改决策。
4. `docs/conventions/security-guardrails.md`（四道闸门第四道 = 恢复验证：回查指标，未恢复回退/转人工）。
5. runbook 定稿（issue 01 产物）：`remediation/runbooks/cpu-spike.md`（verification = 非 DB 路径 P95 ≤0.05s、窗 60s；**rollback=[]** → executing→escalated 直边）与 `slow-sql.md`（verification = `demo_db_pool_used` 回落池上限内、窗 60s；rollback = `mysql.kill_session $session_id` 幂等重试，需 `runtime_values` 注入）。
6. 代码先例（实现前必读，按此顺序）：
   - `src/oncall/remediation/verifier.py`（issue 04 产物，现 24 行）—— **`RecoveryVerifier` Protocol 已在此**：真实验证器**同文件落位**且必须满足该 Protocol（`verify(dry_run_json) -> dict`，返回含 `recovered` 布尔 + promql 结果/condition 判定/时间窗，落 `verify_result_json`）
   - `src/oncall/remediation/executor.py`（issue 05 产物）—— `ControlledExecutor` 复用点：rollback 序列经白名单执行器执行（`runtime_values` 注入先例）；审计返回形状 `{executed, rejected, ok, output_summary}` 对账
   - `src/oncall/api/remediation.py`（issue 04 产物）—— confirm 同步链现状：`verify.get("recovered")` → `mark_recovered`，否则 `mark_failed`；**本票要把「未恢复 → 回滚 → 复验 → recovered/escalated」分叉落进这条链**。归属裁决：编排函数放 remediation 层（service.py 或 verifier 模块），api 层只改调用点、一行不重写状态逻辑；落点拿不准 → 停手问用户
   - `src/oncall/remediation/service.py` —— `mark_recovered / mark_rolled_back / escalate` 迁移封装（executing 出边已含全部终态）；`rollback_status` 字段落点
   - `src/oncall/context/promql.py` —— PromClient / Fetcher 注入先例（M3 取证同款接缝）；remediation 零 import harness（C3），context/infra 依赖是否合法以 import-linter 现契约为准，拿不准问
   - `tests/unit/test_remediation_executor.py` / `test_remediation_api.py` —— 测试组织先例（替身注入 + 调用断言）
7. 踩坑预铺：C8 模块级常量 `UPPER: Final[...]`；同文件多次 Edit 串行 + grep 复核（verifier.py 在 Protocol 文件上扩写）；A1 测试禁 import httpx/requests/openai 等 SDK（Fetcher 替身用普通类/闭包）；60s 观察窗**不真实 sleep**——替身直接返回窗口快照，真实等待归 08。

## 2. 任务（交付物）

### A. `src/oncall/remediation/verifier.py`（同文件扩写，Protocol 之后落真实实现，预算合计 ≈120 行）

- 真实恢复验证器，**满足既有 `RecoveryVerifier` Protocol**：`verify(dry_run_json) -> dict`。
- 判据来源（D-44/G6）：runbook `verification` 显式声明 `{promql, condition, window_s}`——runbook 定位经注入接缝（照 issue 02 `RunbookLoader` 先例，remediation→runbook 模块内自取或注入，C3 零 harness）；dry_run_json 携带 `runbook_slug`。
- PromQL 回查经注入替身断言两条路径（恢复/未恢复），不依赖活 Prometheus；condition 机械判定（≤/< 等简单比较符解析，形状本票定稿并回填）。
- 返回 dict 形状定稿并回填（建议 `{"recovered": bool, "promql": ..., "observed": ..., "condition": ..., "window_s": ...}`——与 issue 04 落位注记「verify_result_json 字段全落」对账）。
- **观察窗不真实等待**：替身返回窗口内快照；真实窗口采样归 08。

### B. 回滚编排（D-45/G7/D-28）

- 未恢复路径：自动执行 runbook 显式 `rollback`（白名单原子操作序列）→ **经 issue 05 `ControlledExecutor` 执行**（回滚同样过白名单 + 留痕）→ 复验（再 verify 一次）：
  - 复验通过 → proposal 收尾 `recovered`（rollback_status 落值定稿并回填，建议 `rolled_back`）
  - 复验仍失败 → proposal 落 `escalated`（D-28：转人工不是丢弃，incident 保持 investigating）
- **不做逆操作推导**：rollback 只来自 runbook 显式定义；cpu-spike `rollback=[]` → 不回滚直接 executing→escalated 直边（service.escalate 已就位）。
- rollback 命令白名单校验失败 → 不执行 + 审计留痕（executor 返回 dict 已含）+ 转人工（不回滚的兜底）。
- 编排落点：remediation 层函数（api 层最小调用，见 §1.6 裁决）；slow-sql rollback 的 `$session_id` 经 `runtime_values` 注入（与 actions 同一来源，形状定稿回填）。

### C. incident 状态翻转

- 恢复路径：proposal `recovered` + incident 翻 `mitigated`（既有枚举 investigating/mitigated/closed，D-19/D-46；翻转经既有 incident service/直接模型更新，api 层不自行查表）。
- escalated / rolled_back 路径：incident 保持 investigating（D-28/D-45）。

### D. 测试（TDD 红绿）

- `tests/unit/test_remediation_verifier.py`：Fetcher 替身恢复路径（判据满足 → recovered=true + verify_result_json 全字段）；未恢复路径；condition 判定边界（=阈值/超窗）；runbook 缺 verification 字段拒绝（脏 runbook 不进验证面）。
- 回滚编排测试（并入上一文件或独立 `test_remediation_rollback.py`，实现票定）：未恢复 → rollback 替身断言过白名单执行器（argv/审计）→ 复验通过 → recovered；复验仍失败 → escalated + incident 保持 investigating；rollback=[] → 直边 escalated；白名单拒绝 → 不执行 + 审计 + escalated；$session_id runtime_values 注入断言。
- api 层回归：confirm approve 同步链改动后既有 `test_remediation_api.py` 零回退（替身 verifier/executor 注入语义兼容）。

### E. 回写（随本票同一个 feature commit）

- issue 06 票：`Status: ready-for-agent → resolved`、验收 6 条打勾、落位注记回填（**verifier 判定形状、rollback_status 取值、回滚分叉编排落点、api 层改动范围、基线数字修正为实际**）。
- `.scratch/m5-remediation-gates/spec.md`：06 行就绪态 → `resolved`。
- CONTEXT.md：无新共享概念不加；若「回滚编排」等成为共享词汇当场入表（硬规 12）。
- decisions.md：不新增 D；编排落点若被用户裁决为评审级变化才登记。
- `docs/architecture/architecture.md`：无 API 清单节（issue 04 已确认），不动。

## 3. 边界（明确不做）

- ❌ 不接真实 Prometheus / 不真实 sleep 观察窗（活栈真实验证留 issue 08）。
- ❌ 不做 M8 UI、不做提案过期（D-40）、不改 runbook 解析器与 runbook 源（runbook 与白名单/verifier 对不上 → 停手问用户）。
- ❌ 不改 `service.py` 状态机合法迁移表（executing 出边已够用）；api 层 confirm 链只改编排调用点，一行不重写状态逻辑。
- ❌ 不做 create_app 真实 executor/verifier 装配（归 08 统一收口）。
- ❌ 零 LLM / 零真实 HTTP 外呼 / 零新依赖。
- ❌ 不把 `.workbuddy/`、`.scratch/tmp/` 卷进 commit。

## 4. 已知裁决与踩坑（预铺，实现时留痕）

1. **判据来源是本票的灵魂**（D-44/G6）：验证器只做机械判定，判据永远是 runbook verification 显式声明——任何「从锚定告警解析阈值」「从调查结论推导判据」的捷径都是伪权威（R3/D-18 教训）。
2. **回滚与 actions 同执行面**（G7）：rollback 序列必须经 issue 05 `ControlledExecutor`（过白名单 + 留审计），不复刻第二条 subprocess 路径。
3. **转人工语义**（D-28）：escalated 落点 = proposal 落 escalated + incident 保持 investigating + 全程可查；复验失败不是 bug 是设计路径，测试按一等公民覆盖。
4. **api 层分叉改动归属**：issue 05 派工 prompt 的「不改 api confirm 链」边界是 05 票内的；本票按 D-40 需把回滚分叉接进同步链——编排函数放 remediation 层，api 只换调用点；若你判断需要更大面积改动 → 停手问用户。
5. **基线数字**：票面「541/10」是 M3 末期旧值；实测当前 649/10。实现后按实际（649 + 本票新增）更新票面该行。
6. **C8/C3 踩坑**：模块级常量 `UPPER: Final[...]`；同文件多次编辑串行 + grep 复核 Protocol 定义仍在；remediation 零 import harness（C3），context 依赖先查 import-linter 契约再动手。
7. **提交粒度**：一个 feature commit（`feat(M5-处置闸门): ...`，scope 与 issue 01–05 一致）含代码 + 测试 + 票面更新；body 写为什么；`Closes .scratch/m5-remediation-gates/issues/06-recovery-verification-rollback.md`。不跳过 hooks。

## 5. 验证清单（门禁，逐条过）

- [ ] TDD：先写测试跑红 → 实现跑绿
- [ ] 全量 `python -m pytest tests`：`649 passed / 10 skipped` → 只增不回退
- [ ] `ruff check .` 与 `ruff format --check .` 全绿
- [ ] 架构守卫全绿（remediation 零 import harness、新文件 ≤300、无 shell=True）
- [ ] A6 bandit 扫描绿（pyproject 现行 skips 不放宽）
- [ ] issue 06 验收 6 条逐条打勾
- [ ] 收尾汇报给用户确认后入库：verifier 判定形状、rollback_status 取值、编排落点、api 改动范围、验收计数实际值
