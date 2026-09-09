# M4-01 派工 prompt：ORM 三表（T1 / G1–G3；D-30/D-31/D-32）

oncall-copilot M4「证据链与过程存储」第 1 票：把 M3 内存契约落库为 ORM 三表（`investigations` + `evidence_steps` + `hypotheses`），只扩 `src/oncall/db/models.py` + 新增单测。**严格 TDD；本票零 src 逻辑变更、零 LLM 调用、零 HTTP；不写任何落库接缝（写入接缝归 02）。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`b4f3536`**，工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests --tb=no -p no:warnings -q`（当前 **479 passed / 7 skipped，coverage 98.05%**）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest 输出用 `> file; PYEXIT=$?` 取退出码（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key；SQLite 现库 `oncall.db` 在仓库根（测试用内存/临时库，**勿写坏现库文件**）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：调查记录 / Investigation Record、证据仓库 / Evidence Repository、证据步 / Evidence Step、假设（confirmed/rejected）、事故 / Incident；**新术语当场入表并写 `_Avoid_`**）
3. `.scratch/m4-evidence-chain/issues/01-orm-three-tables.md`（**权威票面**，下方为摘要）+ `.scratch/m4-evidence-chain/spec.md`「关键契约」节
4. `docs/design/m4-evidence-chain-design.md`（**权威设计，status: reviewed**）——重点：§技术方案-数据模型变更（三表列定义权威）、开放点 G1/G2/G3 定案、§开发计划 T1 行
5. `docs/design/decisions.md`：**D-30（现库加表 + create_all 幂等，Alembic 延至 MySQL）、D-31（investigations 第七表，incident 1:1 唯一覆盖）、D-32（input 只落 input_json，不设 input_summary）**；冻结面 D-19（incidents 五字段不扩列）、D-25（M3/M4 边界：内存契约不倒改）
6. `docs/architecture/architecture.md` §4（`evidence_steps` / `hypotheses` **冻结列逐字段定义——列名与类型必须逐列对齐，不得擅改**（D-25））
7. 代码先例（照抄结构，不抄业务）：`src/oncall/db/models.py` 全文（现 89 行——`Base` 复用、`CheckConstraint` 先例 `ck_alert_events_status`、`Incident` 表即 FK「一」端；扩后 ≈210 行 < C6 300）
8. 测试基建先例：`tests/unit/test_db_alert_events.py`（DB 层单测的建库/断言模式照此）；`tests/unit/` **不是包**，裸 import conftest；autouse 断网 fixture 天然满足本票

## 2. 任务（权威票面 = `.scratch/m4-evidence-chain/issues/01-orm-three-tables.md`，下方为摘要）

落位：只改 `src/oncall/db/models.py`（走现有 `Base` + `create_all` 幂等，D-30）+ 新增 `tests/unit/test_db_evidence_models.py`。

- **`investigations` 表**（D-31，第七表）：`id, incident_id(FK→incidents.id), status(running/concluded/escalated/aborted), stop_reason, conclusion, failure_mode, step_count, total_tokens, total_cost_cny, started_at, finished_at`；`incident_id` **唯一约束**（重复调查覆盖旧行，对齐现进程内注册表语义）；status CHECK 约束照 `ck_alert_events_status` 先例落 DB 层
- **`evidence_steps` 表**（架构 §4 冻结列照抄）：`id, incident_id(FK), step_no, thought, tool, input_json, output_json, output_summary, tokens, cost, latency_ms, ts`；`(incident_id, step_no)` 组合唯一
- **`hypotheses` 表**（架构 §4 冻结列照抄）：`id, incident_id(FK), text, status(confirmed/rejected/active), supporting_steps[], against_steps[]`（数组列照现库 JSON/ARRAY 用法先例，SQLite 下用 JSON）
- **input 侧口径（D-32）**：只落 `input_json` 全文，**不设 `input_summary` 列**；output 侧 `output_json`/`output_summary` 双存照冻结
- 第七表超出架构 §4 六表规划是**已评审偏差**（D-31）：本票不改架构文档，回写归后续票

TDD 顺序建议：列集合断言 → create_all 幂等 → FK 拒绝 → incident_id 唯一冲突 → status CHECK 拒绝 → (incident_id, step_no) 组合唯一。

## 3. 边界（勿越）

- 只改 `src/oncall/db/models.py` + 新建 `tests/unit/test_db_evidence_models.py`；**不改任何其他生产代码、不改 pyproject、不改架构文档**
- 不写落库接缝/不 import 到 harness（写入接缝与 Evidence Repository 归 02 票；`db/evidence_repo.py` 本票不建）
- 冻结列字段名与架构 §4 逐字段一致——文档是权威，不得擅改（D-25）；D-19 incidents 五字段不扩列
- 零 LLM 真实调用、零 HTTP；holdout/（`datasets/golden/holdout/`）禁读
- C6 单文件 ≤300 行（models.py 扩后 ≈210 行，超了说明写多了）；C8 模块级可变全局禁用；命名一律 CONTEXT.md 词汇（调查记录/证据步/假设），不造新词
- 提交规范：中文 + type 前缀，预期 `feat(M4-证据链): ...`；body 写**为什么**；引用 `.scratch/m4-evidence-chain/issues/01-orm-three-tables.md`；不跳过 hooks

## 4. 已知踩坑（M0–M4 实录）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘（models.py 多表追加时尤其注意）
- ② ruff：`max-args=5`、PLR0912 分支 ≤12、PLR0913 参数 ≤5、函数 ≤50 语句
- ③ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture
- ④ SQLite FK 默认**不强制**，测试 FK 拒绝需 `PRAGMA foreign_keys=ON`（或用事件监听，照现有 conftest/engine 先例——先读 `tests/unit/test_db_alert_events.py` 怎么处理）
- ⑤ StrEnum/枚举值小写串，保证 CHECK 约束值与 status 语义一致（running/concluded/escalated/aborted）
- ⑥ pytest 输出统计行用重定向 + 退出码取，别用管道 grep 吞退出码

## 5. 验证路径（收尾清单）

- [ ] issue 01 验收五条逐项打勾（create_all 二次执行幂等 / FK 拒绝 + incident_id 唯一冲突 / status CHECK 拒绝 / 三表列集合与设计文档「数据模型变更」节逐字段一致断言 / 全量门禁不回退），在 issue 文件内回填注记
- [ ] 架构守卫全绿：`tests/test_architecture_guards.py` 全绿（C3/C4/C5/C6/A2）
- [ ] 门禁：pytest（基线 479 passed / 7 skipped **只增不减**）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；`.scratch/m4-evidence-chain/spec.md` 任务序列表 01 行勾选
- [ ] 收尾汇报：落位文件清单 + 测试计数 + 三表列集合与架构 §4 逐字段对齐说明 + 下一票（02 证据仓库写入接缝，blocked by 01 已解除；05/06 并行前沿）就绪确认
