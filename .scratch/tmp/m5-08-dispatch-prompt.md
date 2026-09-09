# M5 issue 08 派工 prompt（T8 真实 e2e 与收尾）

执行 oncall-copilot M5 issue 08：活 demo 栈 2 剧本真实 e2e + 收尾。

## 环境与基线
- 仓库：`F:\Git repository\oncall-copilot`（main，HEAD `f9c064a`）；Python/工具全用绝对路径（venv `C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/`）
- 门禁基线：pytest **679 passed / 10 skipped** 只增不减 + ruff 双检 + import-linter C3–C6 + bandit 全绿
- **开工门槛**：先确认活 demo 栈就绪——9-container docker compose 已起、Prometheus 可查（若未起/不健康，先排查启动，勿硬闯）

## 必读（顺序）
1. 票：`.scratch/m5-remediation-gates/issues/08-real-e2e-closeout.md`
2. 设计：`docs/design/m5-remediation-gates-design.md`（G1–G10 / T8 节 + 验收标准节）
3. 决策：`docs/design/decisions.md` D-39–D-48；术语以 `CONTEXT.md` 为准
4. 先例与接缝：`tests/e2e/test_m5_mock_e2e.py`（mock e2e 驱动方式，换真实组件）、`chaos/scenarios/01-cpu-spike + 02-slow-sql`（注入/cleanup）、`remediation/runbooks/*.md`

## 任务（严格 TDD；管线零 LLM，mock 决策脚本驱动 Planner）
1. 注入 cpu-spike 故障 → mock 决策调查收束干跑 → curl confirm approve → 真实受控执行（demo 容器）→ 恢复验证通过 → incident 翻 `mitigated`；slow-sql 同
2. 未恢复路径若实测触发：回滚 → 仍失败 → escalated 实录
3. **实测回填（禁虚构）**：2 剧本处置耗时 / 执行命令数 / 恢复窗口 → 设计文档验收节 + issue Comments
4. 收尾：设计文档翻 `implemented`；D-39–D-48 落位核对；架构 §4 八表 + §3.3 L2 + §5 时序回写核对；全量门禁终检

## 边界与纪律
- 写操作仅 demo 容器内 + 白名单动作族；最坏情况重建 demo 栈（blast radius 封顶）
- 实测数字一律真跑后回填，禁止估算/捏造
- 注意 issue 07 已修正 cpu-spike runbook：第 3 步 `cores: $cpuset_cores`，执行时经 runtime_values 注入实际原核值
- 文件 ≤300 行、架构守卫不放宽、提交规范中文 + type 前缀 + issue 路径引用

## 验收（可机械判定）
- [ ] 2 剧本端到端：proposal 收尾 recovered + incident mitigated（实录留 issue Comments）
- [ ] 实测回填完整落设计文档验收节
- [ ] 设计文档 status → `implemented`
- [ ] D-39–D-48 落位核对完成（漂移项如实注记）
- [ ] 架构 §4/§3.3/§5 核对落位
- [ ] 全量门禁终检全绿（679 基线只增不减）

## 汇报格式
表格分项：2 剧本实测数据（耗时/命令数/恢复窗口）+ 验收清单勾选 + 变更清单，待确认后入库。
