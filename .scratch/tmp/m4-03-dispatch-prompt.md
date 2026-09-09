# M4-03 派工 prompt：报告读库与 JSON 形状定案（T3 / G6；D-35/D-31）

oncall-copilot M4「证据链与过程存储」第 3 票：`GET /investigations/{incident_id}`
从进程内报告注册表**换读库**（D-25 承诺「M4 落库后整体替换为读表」），读库序列化
落 `db/views.py`，JSON 形状以 M3 `build_report` 现契约为准（D-35，只换数据来源、
**不改键名、不增删键**）；同步修订 `docs/architecture/agent-loop-design.md` 示例键名
（消除双权威）。
**严格 TDD；零 LLM 真实调用、零 HTTP 外呼；不碰 Markdown 导出（归 04）、不碰
opening 视图（归 05）、不碰 schema 摘要（归 06）。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`e4cedec`**（M4-02 步进即写接缝已落位），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests --tb=no -p no:warnings -q`（当前 **509 passed / 7 skipped，coverage 98%**）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest 输出用 `> file; PYEXIT=$?` 取退出码（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key；测试一律内存 SQLite（**勿写坏仓库根的现库 `oncall.db` 文件**）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：证据仓库 / Evidence Repository、调查记录 / Investigation Record、报告出口；本票**无新术语**，若确需造词当场入表并写 `_Avoid_`）
3. `.scratch/m4-evidence-chain/issues/03-report-read-db-json.md`（**权威票面**，下方为摘要）+ `.scratch/m4-evidence-chain/spec.md`「关键契约」节
4. `docs/design/m4-evidence-chain-design.md`（**权威设计，status: reviewed**）——重点：§技术方案一句话概括、§设计的模块「报告读库与导出」行、§API 变更表（GET 改造 + 404 语义）、开放点 G6 定案（= D-35）、§开发计划 T3 行
5. `docs/design/decisions.md`：**D-35（JSON 形状权威 = M3 `build_report` 现形状，键集合契约测试精确守卫，不做导出层键名映射）+ D-31（investigations 1:1 覆盖语义——读库天然取到最近一次）+ D-28（escalated 报告同经 GET 出口，转人工不是丢弃）+ D-25（M3/M4 边界：报告键集合不倒改）**；冻结面 D-17（卡片 13 键）、D-19
6. 代码（行号以基线 `e4cedec` 为准）：
   - `src/oncall/api/investigation.py` 全文（166 行）——重点：`build_report`（55–80，JSON 形状权威）、`InvestigationReportStore`（83–96，**本票退役对象**）、`InvestigationDeps.store` 字段（109）、`investigation_report` GET 端点（156–162，现读注册表）
   - `src/oncall/db/evidence_repo.py` 全文（M4-02 落位：`begin` 覆盖清理 / `finalize` 终态写行——读库的数据即来自这里写的三表）
   - `src/oncall/db/models.py`（三表契约：`Investigation` 会话级字段 / `EvidenceStep` 冻结列 / `Hypothesis`；**注意 investigations 表没有 opening_card 列**）
   - `src/oncall/db/views.py`（现 54 行，读库序列化器的落位文件）
   - `src/oncall/api/app.py`（组装点：`opening_builder` 如何配置——重建 D-17 卡片的现成通道）
   - `tests/unit/test_investigation_api.py` 全文（GET 契约测试现状：键集合守卫、404、escalated 可查、重复调查覆盖——**本票要把这些测试的数据来源从注册表切换到读库**）；`tests/unit/test_db_evidence_models.py` + `tests/unit/test_db_evidence_repo.py`（engine fixture / PRAGMA 先例）
7. `docs/architecture/agent-loop-design.md` §证据链数据形状（**待修订示例**：`step→step_no`、`input→input_json`、`cost→cost_cny`、`supporting→supporting_steps`、`against→against_steps`）

## 2. 任务（权威票面 = issue 03，下方为摘要）

落位：改造 `src/oncall/api/investigation.py`（注册表退役 + GET 读库）+ 扩 `src/oncall/db/views.py`（读库序列化器）+ 修订 `docs/architecture/agent-loop-design.md` 示例 + 测试更新/新增。

- **GET 读库（D-25/D-35）**：`investigation_report` 改为读三表——`investigations` 行（termination/conclusion/failure_mode/step_count/total_tokens/total_cost_cny/stop_reason）+ `evidence_steps` 按 step_no 排序 + `hypotheses` 按入池序；序列化器落 `views.py`，api 只组装；无调查记录 → 404（语义不变）；**键集合与 `build_report` 现契约逐键一致**（含 `confidence` 现口径：已裁决假设 confirmed 占比、active 不计、无已裁决 → 0.0）
- **注册表退役**：`InvestigationReportStore` 与 `InvestigationDeps.store` 移除（D-25「整体替换」）；`build_report` 函数本身是否保留由你裁决——若保留作写路径内存导出的对账基准（roundtrip 测试用），在提交 body 写明理由；POST /investigate 响应形状不变
- **opening_card 来源（票面开放点，倾向 A）**：
  - A：从 `alert_events` + `incidents.alert_ids[0]` **重建 D-17 卡片**（复用现 `opening_builder` 通道，读路径组装）——零 schema 变更，D-17 键集合现成守卫；倾向此项
  - B：`investigations` 表加 `opening_card_json` JSON 列（写时留存）——涉及 models.py 扩列（超出设计文档 §数据模型变更清单，须在提交 body 论证并注明）
  - 二选一，裁决与理由写入提交 body；无论选哪边，写库 → 读库的 opening_card 必须逐键一致
- **roundtrip 对账（回归锚）**：mock 调查写库后经 GET 读库，报告与内存导出（`build_report` 口径）逐字段一致——这是本票的核心测试
- **文档回写（D-35）**：agent-loop-design 示例键名对齐冻结契约，diff 随本票提交、交用户复核；除此之外不动其他架构措辞

TDD 顺序建议：views 序列化器单测（三表行 → 报告 dict 逐键）→ GET 读库 200 + 键集合精确守卫（沿用现 REPORT_KEYS 断言）→ roundtrip 逐字段一致 → 无记录 404 / escalated 可查 / 重复调查读到最新一次 → POST /investigate 既有契约测试全绿（数据来源已换库）→ agent-loop-design 示例修订 → 全量门禁。

## 3. 边界（勿越）

- 生产代码只动：`api/investigation.py`（GET 换读库 + store 退役）、`db/views.py`（序列化器）；若 opening_card 选 B 才允许动 `db/models.py`（须论证）
- **不改**：`harness/`（loop.py/session.py/evidence_repo.py 均不动——写路径已在 02 冻结）、`build_report` 的键集合与键名、`GET` 的 URL 与状态码语义、report.md（归 04）
- `views.py` 现 54 行，扩序列化器后留意 C6 ≤300（预计 ≈150 行内，无压力）
- ruff：`max-args=5`、PLR0912 分支 ≤12、PLR0913 参数 ≤5、函数 ≤50 语句；C6 单文件 ≤300 行；C8 模块级可变全局禁用
- 零 LLM 真实调用、零 HTTP 外呼；`datasets/golden/holdout/` 禁读
- 提交规范：中文 + type 前缀，预期 `feat(M4-证据链): ...`；body 写**为什么**（含 opening_card 来源与 build_report 去留两项裁决理由）；引用 `.scratch/m4-evidence-chain/issues/03-report-read-db-json.md`；不跳过 hooks

## 4. 已知踩坑（M0–M4 实录，含 02 票新增）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ② ruff：`max-args=5`、PLR0912 ≤12、PLR0913 ≤5、函数 ≤50 语句；`_execute_step` 式 return 计数受 PLR0911（≤6）约束——新写函数出口多时先数一数
- ③ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture；API 测试用 `pytest.mark.inproc_asgi`
- ④ SQLite FK 默认不强制——测试 engine 需 `PRAGMA foreign_keys=ON` 事件监听（照 `test_db_evidence_models.py` 先例，直接抄）
- ⑤ StrEnum 值归一：读库取回的 status 是小写串，与报告 `termination`（`result.status.value`）天然一致，无需再映射；反之写报告时勿二次包装
- ⑥ pytest 输出统计行用重定向 + 退出码取，别用管道 grep 吞退出码
- ⑦ `LoopComponents` / `InvestigationDeps` 均为 frozen dataclass——删 `store` 字段时同步清理 `field(default_factory=...)` import，防 F401
- ⑧ ORM 与内存契约同名（`db.models.EvidenceStep` vs `harness.session.EvidenceStep`）——views.py 里给 ORM 侧用别名（照 evidence_repo 的 `as EvidenceStepRow` 先例），防混
- ⑨ 内存 SQLite 每 fixture 独立：roundtrip 测试在同一 engine 上「写 → 读」，engine fixture 须带 `create_tables` + StaticPool 或同连接复用（照 test_investigation_api `_make_engine` 先例）
- ⑩ `confidence` 计算依赖假设终态：02 票 finalize 已把裁决状态同步进 hypotheses 行——读库侧直接按行内 status 计算，勿再引入内存态
- ⑪ 重复调查覆盖后，`GET` 读到的是**唯一一行**（investigations.incident_id 唯一约束）+ 该 incident 的最新证据行——02 票 begin 清理已保证，测试直接断言即可

## 5. 验证路径（收尾清单）

- [ ] issue 03 验收五条逐项打勾（roundtrip 逐字段一致 / 键集合守卫 / 404+escalated+重复调查读最新 / agent-loop-design 示例修订落盘 / 全量门禁），在 issue 文件内回填注记
- [ ] 架构守卫全绿：`tests/test_architecture_guards.py` 全绿（C3/C4/C5/C6/A2/C8）
- [ ] 门禁：pytest（基线 **509 passed / 7 skipped 只增不减**，coverage ≥98%）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；`.scratch/m4-evidence-chain/spec.md` 任务序列表 03 行勾选
- [ ] 收尾汇报：落位文件清单 + 测试计数 + opening_card 来源与 build_report 去留两项裁决说明 + agent-loop-design 修订 diff 摘要（交用户复核）+ 下一票（04 Markdown 导出，blocked by 03 已解除）就绪确认
