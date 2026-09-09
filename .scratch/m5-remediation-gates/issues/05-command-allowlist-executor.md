Status: resolved
Blocked by: 03

# 05 命令白名单 + 受控执行器（T5 / G4 / D-42）

## 任务

- 新建 `src/oncall/remediation/allowlist.py`（行数预算 ≈130）：**静态原子操作表**——动作类型 → 受限命令模板 + 参数白名单正则；初始两族（D-47 剧本对齐）：
  - docker 族：如 `container.remove(name=[a-z0-9_-]{1,64})`（cpu-spike 恢复 = 恢复 cpuset + 停 stress 容器，具体原子操作以 runbook 定稿为准）
  - mysql 族：如 `mysql.kill_session(container, session_id=int)`（slow-sql KILL 锁会话，幂等可重试）
- 新建 `src/oncall/remediation/executor.py`（行数预算 ≈150）：**受控执行器**——demo 容器内执行：`subprocess.run([...], shell=False)` 列表参数（**禁 shell 拼串，A6**）、30s 超时、输出限长；仅接受白名单校验后的命令
- 执行审计：每条白名单命令落审计（含校验前后参数）

## 要点

- **命令字符串永不来自 runbook 正文或模型自由文本**（D-42）——runbook action 只引用白名单内原子操作，命令由模板 + 白名单正则参数渲染
- 参数校验失败 → 拒绝执行 + 审计留痕（白名单外命令/参数/shell 元字符注入样本）
- bandit `S` 系列 + 架构守卫测试双重看管（首个真实 subprocess 外呼面）
- 执行器是 demo 容器外呼面声明：真实面边界照 M3/M4 先例写清（仅 demo 容器、白名单动作族收窄）
- 命令源 = 白名单原子操作（非文档正文/模型文本）——注入面从源头收口（R4）

## 验收（可机械判定）

- [x] pytest 绿：白名单内命令/参数放行（替身 subprocess 断言——本票可注入 subprocess 替身，真实执行留 issue 08）
- [x] pytest 绿：白名单外命令（如任意 `docker rm`）拒绝 + 审计留痕
- [x] pytest 绿：shell 元字符注入样本（`;`/`&&`/`|`/`$()` 等）拒绝 + 审计留痕（参数正则 + 禁 shell 双防线断言）
- [x] pytest 绿：30s 超时（替身挂起 → TimeoutError 归类）、输出限长
- [x] pytest 绿：审计含校验前后参数（命令、参数、decision、ts）
- [x] 架构守卫：A6 bandit S 扫描绿 + 禁 shell=True 的 AST/静态断言（`tests/test_architecture_guards.py::test_no_shell_true`）
- [x] 全量门禁不回退（基线 ~~541/10~~ **修正为 621/10**，M5 issue 04 入库后真值；实测 **649 passed / 10 skipped** = 621 + 本票 28，只增）+ ruff 双检

## 落位注记（实现票回填）

- **原子操作表定稿**（`src/oncall/remediation/allowlist.py::ATOMIC_ACTIONS`，docker/mysql 两族三动作，与 runbook 定稿逐条对账）：
  - `docker.remove_container`：params `{name}`（正则 `[a-z0-9][a-z0-9_-]{0,63}`）→ argv `["docker","rm","-f","{name}"]`
  - `docker.restore_cpuset`：params `{container, cores}`（cores 正则 `[0-9]{1,3}(-[0-9]{1,3})?(,...)*`）→ argv `["docker","update","--cpuset-cpus={cores}","{container}"]`
  - `mysql.kill_session`：params `{container, session_id}`（session_id 正则 `[0-9]{1,10}`）→ argv `["docker","exec","{container}","mysql","-uroot","-e","KILL {session_id}"]`（KILL 语句整体为单个 argv 元素）
  - 校验入口 `render_argv(action, params)`：未登记动作 / 参数缺失 / 白名单外多余参数 / 不匹配正则 / 含 shell 元字符（``;&|`$()<>\'\"\n\r`` 黑名单）→ 抛 `AllowlistViolation` 带拒绝原因；纯函数零 IO 零 subprocess
- **dry_run_json commands[] 形状增补**：issue 02 干跑 handler 每条命令新增 `params` 键（原始参数模板 dict，含 `$var` 原文），随批准对象锁定（D-39）；`command` 字段仍是预览，永不进入执行（D-42）
- **受控执行器定稿**（`src/oncall/remediation/executor.py::ControlledExecutor`，满足既有 `RemediationExecutor` Protocol，同文件 Protocol 之后落位）：
  - 构造注入：`runner`（默认 `subprocess.run(argv, shell=False, check=False, timeout=30, capture_output=True)`）+ `runtime_values`（`$var` 执行期注入映射）+ `timeout_s`（默认 30.0）
  - 流程：commands[] → `$var` 注入（未注入 → 拒绝留痕）→ `render_argv` 白名单校验渲染 → 仅接受渲染产物执行
  - **审计形状**（每条记录）：`{step, action, argv(放行时), params_before, params_checked, decision: "allow"|"reject", reason(拒绝时), ts, ok, returncode, stdout, stderr(超时/失败时 error)}`；**返回 dict**：`{"executed": [...], "rejected": [...], "ok": bool, "output_summary": "共 N 条：allow X（ok Y）/ reject Z"}`——落 `params_json["execution"]`（api 层消费）
  - 白名单拒绝不抛异常：拒绝进 `rejected[]` 审计，api 层不感知白名单细节
- **超时/限长参数定稿**：`DEFAULT_TIMEOUT_S = 30.0`（TimeoutExpired 归类为失败步 `ok=false`，不抛出）；`MAX_OUTPUT_BYTES = 4096`（stdout/stderr 各按 bytes 截断后解码）
- **A6 双守卫落位**：ruff S 规则 + bandit（pyproject `[tool.bandit]` 显式豁免 `B404/B603` 并留三重看管理由：白名单渲染 + shell=False + 架构守卫）+ `tests/test_architecture_guards.py::test_no_shell_true`（AST 扫描 src/ 禁 `shell=True` 字面量实参）
- **门禁实测**：全量 `649 passed / 10 skipped`（621 + 本票 28：allowlist 15 + executor 12 + 守卫 1）；ruff check / format --check 全绿；bandit 0 issue；架构守卫 8 passed
