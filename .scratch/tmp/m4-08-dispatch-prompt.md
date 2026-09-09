# M4-08 派工 prompt：真实实测重跑与收尾（T8）

oncall-copilot M4 第 8 票（收官票）：3 剧本**真实 LLM 调用**重跑实测，回填
步数/耗时/成本/Top-1，与 M3 基线对比记录两缺口修复（05 opening 视图 + 06
schema 摘要）的改善幅度，随后设计文档翻 `implemented` 收尾。
**实测数据如实记录、禁虚构；除测量替身组装外零改测试、零改生产代码。**

## 0. 开工门槛（未满足即停手，不要自行开跑）

- **真实调用前需用户确认 key 并授权预算**（照 M3 issue 08 流程）：`ONCALL_LLM_*`
  env / `~/.oncall-llm-env` 三件套（base_url / api_key / model），模型 qwen3.7-flash
  JSON Mode。新会话开工第一件事：向用户确认「key 已就位、同意按 M3 口径预算
  （两轮 6 次合计 ≈¥0.008、单次最高 ¥0.004，R9 单价）后再跑」。
- 成本逼近 **¥0.5 上限即停手上报**（实测基线远低于该值，预期不触发）。

## 1. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；
  git 基线 **07 落位后的 HEAD**（开工前 `git log --oneline -1` 确认，预期
  `e24c2c3` test(M4-证据链) 验收收口提交），工作区干净（`.workbuddy` 日志与
  `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：
  `C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（07 落位后实测，**只增不减**）：**541 passed / 7 skipped /
  coverage 98%**（pyproject 门禁线 ≥80%）+ ruff check + ruff format --check 全绿；
  架构守卫 7 passed。注意 `tests/integration/` 的真实 e2e 默认 skip（7 skipped
  中的主力），**本票跑真实轮时它们会转 passed——门禁对账时按 skip 数变化如实
  说明，勿当回退**
- pytest 统计行：pyproject addopts 已含 `-q`，命令行勿再传 `-q`；统计行核对用
  重定向 + 退出码
- `datasets/golden/holdout/` 禁读；mock 期一律用内存 SQLite，**勿写坏仓库根
  `oncall.db`**；真实轮落库走临时库文件或内存库（见 §3），不碰根库

## 2. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票复用既有词汇，**新术语当场入表**）
3. `.scratch/m4-evidence-chain/issues/08-real-rerun-closeout.md`（**权威票面**）
   + `spec.md` 任务序列表 08 行
4. `docs/design/m4-evidence-chain-design.md`——「验收标准」节（回填对象）+
   §开放设计点 G8/G9 定案（05/06 修复内容 = 本票核心观测项）
5. `docs/design/decisions.md`：D-18（golden 替身取证面）、D-28（终止语义）、
   D-29（规则判分）、D-38（schema 摘要）；M3 issue 08 注记（基线口径来源）
6. **M3 真实实测先例**：`tests/integration/test_planner_real_e2e.py`（`ONCALL_RUN_LLM_E2E=1`
   门控 + `OpenAIPlannerClient.from_env()` + usage 明细采集的完整先例）
   + `.scratch/m3-investigation-loop/issues/08-e2e-validation.md`（基线：首版
   0/3，主失败 = 参数级失败 + 同参绕圈）
7. `tests/e2e/test_m4_acceptance_persistence.py` + `tests/golden_support.py`
   （3 剧本夹具与 `make_e2e_components` 组装——真实轮把 MockPlanner 换成
   `OpenAIPlannerClient`，取证面 Fetcher 替身不动）
8. `docs/conventions/`（工程纪律权威）

## 3. 任务（权威票面 = issue 08，下方为摘要）

1. **真实轮 harness**：照 M3 先例组装「真实 Planner + golden Fetcher 替身 +
   Verifier + EvidenceRepository」经 `POST /investigate` 走 3 剧本
   （cpu-spike / slow-sql / queue-backlog）——D-18 替身不含 root_cause/
   investigation_path/remediation（防泄漏）；落库链路全程生效，真实轮即
   「100% 落库」实战验证。mock 契约测试**零改动**（热切换先例）
2. **逐剧本回填**（禁虚构）：步数 / 耗时 / 成本（usage 明细）/ 结论 / Top-1
   命中（D-29 规则匹配级判定，口径复核归 M7）/ 参数级失败率 / 畸形率
   （JSON 契约）；usage 明细落 `.scratch/tmp/`（**不进版本库**）
3. **对比 M3 基线**：结论落 issue 08 Comments——改善/未改善均如实记录；
   **未改善须分析归因并交用户复议**，不许粉饰
4. **收尾**：设计文档「验收标准」节逐条打勾（第 8 条即本票实测数据）→
   文档头翻 `implemented`；`CONTEXT.md` / `decisions.md` 落位核对；
   spec 状态节更新；架构 §4 六表→七表回写与 agent-loop-design 修订
   （03 已带）做落位终核；issue 08 验收打勾 + `Status:` 翻 `resolved`

## 4. 边界（勿越）

- 生产代码零改动（发现 bug 停手上报，勿顺手修）；测试零改动（harness 组装
  放 integration 新文件或已有门控测试的运行，不改契约断言）
- 实测数据**只记不造**：某剧本失败就记失败与 failure_mode，不许重跑挑好看
  的数字（如需重跑，逐次全部记录）
- 零外呼面控制：真实面只有 Planner（`OpenAIPlannerClient` 经 infra 收口），
  取证面仍是替身；断网 fixture 只对 mock 测试生效，真实轮按 M3 先例显式
  门控运行
- 提交规范：中文 + type 前缀（实测数据回填预期 `docs(M4-证据链): ...`，
  若有 integration harness 新文件则拆 `test(M4-证据链): ...`）；body 写**为什么**
  （实测回填是硬规 10「不得虚构」的兑现动作）；引用
  `.scratch/m4-evidence-chain/issues/08-real-rerun-closeout.md`；不跳过 hooks
- 高危 git 命令按 AGENTS.md 硬规 11 先报后动

## 5. 已知踩坑

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ② pytest `-qq` 吞统计行；统计行用重定向 + 退出码核对
- ③ tests/unit 不是包（裸 import conftest）；integration 目录已有先例可照抄
- ④ 真实轮 skip 数会从 7 下降（integration 门控测试转跑）——门禁对账按
  「passed + skipped 总量不减」口径说明，勿当回归
- ⑤ usage 明细 / 实测 raw JSON 只落 `.scratch/tmp/`，commit 前确认未混入
- ⑥ M3 基线口径出处是 M3 issue 08 注记（首版 0/3、≈¥0.008/6 次、步数 2–5），
  引用勿凭记忆改写
- ⑦ escalate/aborted 若真实轮触发，落库可查语义照 D-28（转人工不是丢弃），
  记录时勿另造终止口径

## 6. 验证路径（收尾清单）

- [ ] 用户 key 确认 + 预算授权（开工门槛，未确认不跑真实轮）
- [ ] 3 剧本实测回填完整（步数/耗时/成本/Top-1/参数级失败率/畸形率，逐剧本）
- [ ] 与 M3 基线对比结论落 issue 08 Comments（未改善须归因 + 交复议）
- [ ] 设计文档验收节打勾 + 翻 `implemented`；CONTEXT/decisions/spec 终核
- [ ] 全量 pytest（基线 541/7 只增不减，skip 数变化如实说明）+ coverage ≥80%
      + ruff 双检全绿 + 架构守卫全绿
- [ ] 收尾汇报：逐剧本实测表 + 基线对比表 + 门禁计数对账 + 遗留风险清单
      （M4 里程碑整体收口状态）
