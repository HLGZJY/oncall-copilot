Status: ready-for-agent
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

- [ ] 实测回填完整：2 剧本处置耗时 / 执行命令数 / 恢复窗口 / 验证结果，落设计文档验收节 + 本 issue Comments（禁虚构）
- [ ] 2 剧本端到端处置恢复断言绿（proposal 收尾 recovered + incident mitigated）
- [ ] 设计文档翻 `implemented`（评审人/日期/实测回填）
- [ ] D-39–D-48 落位核对完成（无实现与决策漂移项，漂移项如实注记）
- [ ] 架构 §4 八表 + §3.3 L2 + §5 时序回写/核对落位
- [ ] 全量门禁终检：pytest（只增不减）+ ruff 双检 + import-linter + bandit 全绿

## 落位注记（实现后回填）

- （待实现票回填：2 剧本实测数据、demo 栈确认记录、收尾核对清单）
