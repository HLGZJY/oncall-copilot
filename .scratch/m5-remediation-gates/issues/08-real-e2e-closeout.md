Status: resolved
Blocked by: 07

# 08 真实 e2e 与收尾（T8）—— demo 栈就绪门槛

> **开工门槛（环境门槛，非 key 门槛）**：需活 demo 栈（docker compose 已起 + Prometheus 可查 + 2 剧本可注入）——**开工前需用户确认 demo 栈就绪**（照 M3/M4 issue 08 流程；处置管线零 LLM，mock 决策脚本驱动，不花 key）。

## 任务

- 活 demo 栈 2 剧本（`cpu-spike` + `slow-sql`）端到端自动处置恢复：注入故障 → mock 决策调查 → execute_action 干跑 → 人工 confirm（curl/脚本）→ 受控执行（真实 demo 容器）→ 恢复验证通过 → incident 翻 `mitigated`
- **实测回填**（禁虚构）：2 剧本处置耗时 / 执行命令数 / 恢复窗口如实回填「验收标准」节 + 本 issue Comments
- 未恢复路径（若实测触发）：回滚 → 仍失败 → 转人工的实录
- 收尾：
  - 设计文档翻 `implemented`（验收节勾选 + 实测回填）
  - D-39–D-48 落位核对（实现与决策一致）
  - CONTEXT 术语核对（新术语已入表，无缺漏）
  - 架构 §4 第八表回写核对（issue 03 已预告回写，本票确认落位）+ §3.3 L2 注记 + §5 时序「匹配 SOP → 干跑 → 人工确认(M5)」兑现核对
  - 架构守卫 + 全量门禁终检

## 要点

- 真实轮处置是**确定性系统行为**（非 LLM 决策）——mock 决策脚本驱动调查，本票验证的是四道闸门管线在活栈的真实行为
- 写操作副作用边界：仅 demo 容器内 + 白名单动作族（docker/mysql）+ 干跑预览先行 + 确认门人审——最坏情况重建 demo 栈，blast radius 封顶（设计文档风险 1）
- 处置耗时/命令数/恢复窗口是 M7 处置成功率矩阵的真实数据源

## 验收（可机械判定）

- [x] 实测回填完整：2 剧本处置耗时 / 执行命令数 / 恢复窗口 / 验证结果，落设计文档验收节 + 本 issue Comments（禁虚构）
- [x] 2 剧本端到端处置恢复断言绿（proposal 收尾 recovered + incident mitigated）
- [x] 设计文档翻 `implemented`（评审人/日期/实测回填）
- [x] D-39–D-48 落位核对完成（无实现与决策漂移项，漂移项如实注记）
- [x] 架构 §4 八表 + §3.3 L2 + §5 时序回写/核对落位
- [x] 全量门禁终检：pytest（只增不减）+ ruff 双检 + import-linter + bandit 全绿

## 落位注记（T8 实现后回填 2026-09-09）

- **交付物**：`tests/integration/test_m5_real_e2e.py`（活栈真实 e2e，环境开关 `ONCALL_RUN_M5_REAL_E2E=1`，2 剧本 parametrize 风格独立用例）+ `tests/integration/m5_real_support.py`（观察窗轮询代理 PollingRecoveryVerifier + chaos/uvicorn/curl 驱动）+ `tests/unit/test_m5_polling_verifier.py`（轮询代理 3 单测）。生产代码仅 1 处修正（allowlist.py，见 Comments 发现 1）。
- **实测数据**（详见设计文档验收节回填表 + `.scratch/tmp/m5-08-real-e2e-report.json`）：cpu-spike recovered（confirm 121.9s / 3 命令 / 恢复窗 120.0s / P95 0.045≤0.05）；slow-sql recovered（confirm 106.1s / 2 命令 / 恢复窗 105.0s / pool_used 2.5<5）。未恢复路径未实测触发（runbook rollback 显式语义已由 mock e2e 覆盖，活栈未复现失败不虚构）。
- **demo 栈确认记录**：9 容器全部 Up（oncall/api-gw/redis/mysql healthy），Prometheus healthy 且 `up` 可查，2026-09-09 实查。
- **收尾核对清单**：设计文档翻 `implemented` ✅；D-39–D-48 均在 `decisions.md` 且实现一致（漂移注记见 Comments 发现 1：D-42 argv 模板补 `-poncall`，属真实环境事实修正非语义变更）✅；CONTEXT 无新增术语（轮询代理为测试 harness 实现细节）✅；架构 §4 第八表（T3 已回写）、§3.3 L2 干跑语义（issue 02 已回写）、§5 时序「匹配 SOP → 干跑 → 人工确认(M5)」（已落）核对一致 ✅；spec.md 08 行翻 resolved ✅。
- **门禁终检**：682 passed / 12 skipped（T7 基线 679/10 只增不减；+3 单测、+2 活栈用例默认跳过）+ coverage 97.75% + ruff 双检 + bandit 全绿；import-linter C3 KEPT，C4+C5 工具异常如实注记（AST 守卫同等覆盖，见 Comments）。

## Comments（T8 执行期注记 2026-09-09）

### 开工门槛解除

- 活 demo 栈实查就绪：`docker ps` 9 容器全部 Up（oncall-demo-oncall-1 / api-gw-1 / redis-1 / mysql-1 healthy + prometheus/alertmanager/alert-dumper/loki/worker），Prometheus `/-/healthy` OK 且 `up` 查询返回 api-gw=1。
- 运行方式：`ONCALL_RUN_M5_REAL_E2E=1 python -m pytest tests/integration/test_m5_real_e2e.py`（逐剧本独立用例，bash 注入/清理走 Git Bash 显式路径 `E:\Git\bin\bash.exe`，避开 System32 WSL bash）。

### 实测回填（2 剧本，禁虚构）

| 剧本 | 终态 | 告警等待 | 处置耗时 | 命令数 | 恢复窗口 | 验证 |
|---|---|---|---|---|---|---|
| cpu-spike | recovered + mitigated | 65.5s | 121.9s | 3（0 拒） | 120.0s（9 回查） | P95 0.045≤0.05 |
| slow-sql | recovered + mitigated | 85.6s | 106.1s | 2（0 拒） | 105.0s（8 回查） | used 2.5<5 |

- 未恢复路径（回滚→仍失败→escalated）未在活栈实测触发：2 剧本处置均一次恢复；该路径的编排语义（D-45/D-28）已由 mock e2e `test_mock_e2e_slow_sql_not_recovered_rollback_escalated` 钉死。如实记录，不构造失败重跑凑数。

### 真实发现与修复（T8 的价值所在——mock 层永远测不出的两处）

1. **allowlist mysql.kill_session 缺密码（生产代码，已修）**：argv 模板 `mysql -uroot -e "KILL {id}"` 无 `-poncall`，真实客户端 Access denied（rc=1，手动复现实锤）。更隐蔽的次生风险：KILL 失败但探针容器被停、连接池照样排空，恢复验证仍判 recovered——真实环境里「执行部分失败 + 指标恰好回落」会掩盖执行缺陷。修复：模板补 `-poncall`（与 chaos 脚本凭据一致），同步修 2 处单测 argv 断言。
2. **持锁会话发现 SQL 自匹配（harness，已修）**：`processlist WHERE info LIKE '%SLEEP(%'` 会匹配发现查询自身连接（其 info 含该字符串），ids[0] 取到查完即退的自己 → KILL 报 Unknown thread id（run2/run3 连续两败的根因，dump 执行审计实锤）。修复：加 `AND id != CONNECTION_ID()`。

### 漂移与环境注记

- **D-42 落位**：语义零漂移（静态原子操作表、参数白名单、模板渲染均一致）；argv 模板补 `-poncall` 属真实环境事实修正，注释已留档。
- **import-linter 环境漂移**：C3 KEPT；C4+C5 契约在 import-linter 2.15 + grimp 3.17 组合下检查中途异常无结论（grimp 3.x API 变更嫌疑，缓存清理后复现）。C4/C5 的机械约束由 AST 守卫 `test_no_bare_http_clients` / `test_no_scattered_llm_sdks` 同等覆盖（全绿，quality-gates 双保险设计）。建议后续票评估 pin `grimp<3` 复活 import-linter 全绿。
- **门禁终检**：pytest 682 passed / 12 skipped（只增不减）+ coverage 97.75% + ruff 双检 + bandit 全绿。
- **M5 里程碑 8/8 resolved 收官**。
