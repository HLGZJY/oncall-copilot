Status: ready-for-agent
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

- [ ] pytest 绿：白名单内命令/参数放行（替身 subprocess 断言——本票可注入 subprocess 替身，真实执行留 issue 08）
- [ ] pytest 绿：白名单外命令（如任意 `docker rm`）拒绝 + 审计留痕
- [ ] pytest 绿：shell 元字符注入样本（`;`/`&&`/`|`/`$()` 等）拒绝 + 审计留痕（参数正则 + 禁 shell 双防线断言）
- [ ] pytest 绿：30s 超时（替身挂起 → TimeoutError 归类）、输出限长
- [ ] pytest 绿：审计含校验前后参数（命令、参数、decision、ts）
- [ ] 架构守卫：A6 bandit S 扫描绿 + 禁 shell=True 的 AST/静态断言
- [ ] 全量门禁不回退（基线 541/10）+ ruff 双检

## 落位注记（实现后回填）

- （待实现票回填：原子操作表定稿（docker/mysql 各含哪些动作）、审计形状、超时/限长参数定稿）
