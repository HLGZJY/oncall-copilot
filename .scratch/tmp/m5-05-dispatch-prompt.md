# M5 issue 05 派工 prompt — 命令白名单 + 受控执行器（T5 / G4 / D-42）

> 新开会话执行本票。prompt 完整自洽：含环境、TDD、门禁、设计引用与验收标准，照 issue 01–04 惯例。
> 执行完把「原子操作表定稿、审计形状、超时/限长参数定稿、验收计数实际值」列给用户确认后再入库。

## 0. 环境与仓库基线

- 仓库：`F:\Git repository/oncall-copilot`（Windows 11 / Git Bash；文件与解释器一律绝对路径）
- Python：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（装包走清华源；本票零新依赖——subprocess/审计全 stdlib）
- **基线 HEAD：`58ce4e8`**（前序：`ecd6e99` issue 03 → `58ce4e8` issue 04）。开工先 `git status` 确认工作区干净（仅 `.workbuddy/` 日志与 `.scratch/tmp/` 派工 prompt 属例外，**勿动、勿入库**）。
- 前置依赖已满足：issue 03（proposal 状态机 + service）/ issue 04（确认门 API + `RemediationExecutor` Protocol 接缝）**均已入库** → `Blocked by: 03` 解除。
- **门禁基线实测：`621 passed / 10 skipped`**（issue 04 入库后真值）。⚠️ 票面验收第 7 条写的「基线 541/10」是 M3 末期旧值，以 621/10 为准；实现后按实际数字更新票面该行。
- 硬规：AGENTS.md 12 条 + commit 规范（中文 + type 前缀、body 写为什么、Closes 用 issue 文件路径）；术语一律用 CONTEXT.md 词（命令白名单/受控执行器/处置提案已入表）；TDD 红绿循环；零 LLM、零真实外呼（pytest-socket 已全局断网；subprocess 用替身注入，真实容器执行留 issue 08）。

## 1. 必读（权威源，按顺序）

1. `.scratch/m5-remediation-gates/issues/05-command-allowlist-executor.md` —— **本票权威**，验收 7 条逐条打勾。
2. `docs/design/m5-remediation-gates-design.md`：
   - **§G4 行（约 156 行）**：静态原子操作表定案——白名单 = 动作类型 → 受限命令模板 + 参数白名单正则；runbook action 只可引用表内原子操作，**命令字符串永不来自 runbook 正文或模型自由文本**（R4/OWASP：命令对白名单校验、参数白名单正则、禁 shell 拼接）
   - 模块表 allowlist/executor 两行（预算 ≈130/≈150）+ §C3/C6 论证 4（A6 禁 shell 拼串：`subprocess.run([...], shell=False)` 列表参数 + bandit S 系列 + 架构守卫双重看管）+ 风险 3（首个真实 subprocess 外呼面的收口对策）
3. `docs/design/decisions.md`：**D-42**（命令源收口）/ **D-47**（初始 2 runbook 与剧本对齐——原子操作表以 runbook 定稿为准）。**本票不新增 D 编号**；实现中发现契约缺漏需微调 → 停手问用户走评审，不自行改决策。
4. `docs/conventions/security-guardrails.md`（四道闸门/白名单/操作分级）+ `docs/conventions/quality-gates.md`（C4/C6/A6）—— A6 守卫形态以这两份为准。
5. runbook 定稿（issue 01 产物，**原子操作表的事实来源**）：`remediation/runbooks/` 下 cpu-spike / slow-sql 两个源文件——actions[].steps[].action/params 就是白名单要放行的原子操作引用；rollback 同族。
6. 代码先例（实现前必读，按此顺序）：
   - `src/oncall/remediation/executor.py`（issue 04 产物，现 25 行）—— **`RemediationExecutor` Protocol 已在此**：真实执行器**同文件落位**且必须满足该 Protocol（`execute(dry_run_json) -> dict`）；返回 dict 落 `params_json["execution"]`（api 层消费，键形状本票定稿并回填）
   - `src/oncall/api/remediation.py`（issue 04 产物）—— confirm 同步链里执行器的消费方式（唯一输入 = `dry_run_json` 原文）；**本票不改 api 层装配**（真实 executor 注入 create_app 归 08 统一收口）
   - `src/oncall/harness/tools/execute.py`（issue 02）—— dry_run_json commands[] 形状（`{step, action, command, impact, runtime_params}`）与 `$var` 运行时参数语义（**执行期注入**即本票的参数解析落点）
   - `src/oncall/remediation/service.py` —— 模块 docstring 风格 / `Final` 常量 / 零 harness import 的 C3 纪律
   - `tests/unit/test_remediation_service.py` / `test_execute_action_dryrun.py` —— 测试组织先例
7. 踩坑预铺：C8 模块级常量 `UPPER: Final[...]`（AnnAssign）；同文件多次 Edit 串行 + grep 复核；A1 测试禁 import httpx/requests/openai 等 SDK（subprocess 替身用普通类）。

## 2. 任务（交付物）

### A. `src/oncall/remediation/allowlist.py`（新文件，预算 ≈130 行）

- **静态原子操作表**：动作类型 → 命令模板 + 参数白名单正则。初始两族以 **runbook 定稿**为准（issue 01 产物），至少覆盖：docker 族（`docker.remove_container` / `docker.restore_cpuset`，对照 cpu-spike runbook + cleanup.sh 语义）、mysql 族（`mysql.kill_session`，幂等）。
- 校验入口：给定原子操作名 + 参数 dict → 逐参数过白名单正则 → 返回**渲染后的 argv 列表**（模板 + 校验后参数）；未登记动作 / 参数缺失 / 参数不匹配 / 含 shell 元字符（`;` `&&` `|` `` ` `` `$()` 等）→ 拒绝并给拒绝原因。
- 纯函数、零 IO、零 subprocess——白名单只做判定与渲染（「系统层唯一可执行面」的数据落点）。

### B. `src/oncall/remediation/executor.py`（同文件扩写，Protocol 之后落真实实现，预算合计 ≈150 行）

- 真实受控执行器类，**满足既有 `RemediationExecutor` Protocol**：`execute(dry_run_json) -> dict`。
- 流程：解析 `dry_run_json["commands"]` → 逐条经 allowlist 校验渲染 argv → **仅接受白名单校验后的命令** → 执行。
- **subprocess 注入接缝**（照 issue 02/04 Protocol 先例）：执行器构造时注入 runner（`Callable[[list[str], ...], CompletedProcess 形]`）；默认实现 `subprocess.run(argv, shell=False, timeout=30, capture_output=True)`；测试注入替身断言收到的 argv。**本票不真实外呼**（真实容器执行与 demo 栈对接留 issue 08）。
- 30s 超时（替身挂起 → `TimeoutExpired` 归类为失败步）、输出限长（定稿并回填，建议 stdout/stderr 各 ≤4KB 截断）。
- **执行审计**：每条命令落审计记录——命令（原子操作名）、argv（校验后）、**校验前后参数**、decision（allow/reject + 拒绝原因）、ts；返回 dict 形状定稿并回填（建议 `{"executed": [...], "rejected": [...], "ok": bool, "output_summary": ...}`——与 issue 04 落位注记「执行输出摘要落 params_json['execution']」对账）。
- 白名单拒绝不抛异常：拒绝也进审计并体现在返回 dict（api 层不感知白名单细节）。

### C. 测试（TDD 红绿）

- `tests/unit/test_remediation_allowlist.py`：白名单内命令/参数放行（argv 渲染断言）；白名单外动作拒绝；参数缺失/不匹配拒绝；shell 元字符注入样本（`;`/`&&`/`|`/`$()`/反引号）逐个拒绝。
- `tests/unit/test_remediation_executor.py`（或并入上一文件，实现票定）：替身 runner 放行路径 argv 断言；白名单外拒绝 + 审计留痕（校验前后参数都在）；注入样本拒绝 + 审计留痕（参数正则 + 禁 shell 双防线）；30s 超时归类 + 输出限长截断；审计含命令/参数/decision/ts。
- **禁 shell=True 静态断言**：架构守卫加一条（AST 扫描 src/ 无 `shell=True` 关键字实参；或并入 test_architecture_guards.py 既有类——实现票判断，不确定就问）；bandit A6 按 conventions 的运行方式跑绿（若仓库无 bandit 配置，先查 `pyproject.toml` / Makefile / CI 现状再动手）。

### D. 回写（随本票同一个 feature commit）

- issue 05 票：`Status: ready-for-agent → resolved`、验收 7 条打勾、落位注记回填（**原子操作表定稿（docker/mysql 各含哪些动作与正则）、审计形状、超时/限长参数定稿、基线数字修正为实际**）。
- `.scratch/m5-remediation-gates/spec.md`：05 行就绪态 → `resolved`。
- CONTEXT.md：无新共享概念不加；若「原子操作」等成为共享词汇当场入表（硬规 12）。
- decisions.md：不新增 D；无评审级变化则不动。
- `docs/architecture/architecture.md`：无 API 清单节（issue 04 已确认），不动。

## 3. 边界（明确不做）

- ❌ **不实装恢复验证器**（PromQL 回查归 06，`verifier.py` Protocol 已就位不动）。
- ❌ **不接真实 demo 容器执行**（subprocess 只走替身测试；runner 默认实现可写但不进测试外呼，真实对接与 create_app 装配归 08 统一收口）。
- ❌ 不改 `api/remediation.py` 的 confirm 链语义、不改 `service.py` 状态机、不改 runbook 解析器（白名单只**消费** runbook 定稿的动作引用，发现 runbook 与白名单对不上 → 停手问用户，不擅改 runbook 源）。
- ❌ 不做 M8 UI、不做提案过期（D-40）。
- ❌ 零 LLM / 零真实 HTTP 外呼 / 零新依赖。
- ❌ 不把 `.workbuddy/`、`.scratch/tmp/` 卷进 commit。

## 4. 已知裁决与踩坑（预铺，实现时留痕）

1. **命令源收口是本票的灵魂**（D-42/R4）：runbook/模型/文档永远只提供「原子操作名 + 参数」引用；能变成 argv 的只有 allowlist 模板渲染这一条路。任何「从 dry_run_json.command 字符串直接拆」的捷径都违背设计——`command` 字段是渲染给人看的预览，不是执行输入。
2. **审计双防线**：参数正则（allowlist 层）与禁 shell（subprocess 层）都要有测试断言，缺一不算过验收③。
3. **基线数字**：票面「541/10」是 M3 末期旧值；实测当前 621/10。实现后按实际（621 + 本票新增）更新票面该行。
4. **C8 踩坑**：模块级常量（原子操作表、正则）用 `UPPER: Final[...]`（AnnAssign），勿写裸小写 `= ...`。
5. **同文件并行 Edit 会丢改动**：executor.py 要在 Protocol 文件上扩写，多次编辑必须串行，改完 grep 复核 Protocol 定义仍在。
6. **提交粒度**：一个 feature commit（`feat(M5-处置闸门): ...`，scope 与 issue 01–04 一致）含代码 + 测试 + 票面更新；body 写为什么；`Closes .scratch/m5-remediation-gates/issues/05-command-allowlist-executor.md`。不跳过 hooks。

## 5. 验证清单（门禁，逐条过）

- [ ] TDD：先写测试跑红 → 实现跑绿
- [ ] 全量 `python -m pytest tests`：`621 passed / 10 skipped` → 只增不回退
- [ ] `ruff check .` 与 `ruff format --check .` 全绿
- [ ] 架构守卫（含新增禁 shell=True 断言）全绿；remediation 零 import harness、C6 新文件 ≤300
- [ ] A6 bandit S 扫描绿（按 conventions 现行方式）
- [ ] issue 05 验收 7 条逐条打勾
- [ ] 收尾汇报给用户确认后入库：原子操作表定稿、审计形状、超时/限长定稿、验收计数实际值
