# M5 issue 03 派工 prompt — proposal 落库与状态机（T3 / G8 / D-46）

> 新开会话执行本票。prompt 完整自洽：含环境、TDD、门禁、设计引用与验收标准，照 issue 01/02 惯例。
> 执行完把「迁移表定稿、approved 态语义处理、架构 §4 回写、验收计数实际值」列给用户确认后再入库。

## 0. 环境与仓库基线

- 仓库：`F:\Git repository\oncall-copilot`（Windows 11 / Git Bash；文件与解释器一律绝对路径）
- Python：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（装包走清华源；本票零新依赖，无需装包）
- **基线 HEAD：`914fdc6`**（前序：`369a176` issue 01 → `a2d1369` issue 02 → `914fdc6` worklog）。开工先 `git status` 确认工作区干净（仅 `.workbuddy/` 日志与 `.scratch/tmp/` 派工 prompt 属例外，**勿动、勿入库**）。
- 前置依赖已满足：issue 02（execute_action 干跑 + L2 授权判定器）**已入库** → 本票 `Blocked by: 02` 解除。
- **门禁基线实测：`580 passed / 10 skipped`**（issue 01→02 后真值）。⚠️ 票面验收第 7 条写的「基线 541/10」是 M3 末期旧值，以 580/10 为准；实现后按实际数字更新票面该行。
- 硬规：AGENTS.md 12 条 + commit 规范（中文 + type 前缀、body 写为什么、Closes 用 issue 文件路径）；术语一律用 CONTEXT.md 词（处置提案/干跑预览/恢复判据/受控执行/推理与执行分离已入表）；TDD 红绿循环；测试只住约定接缝上；零 LLM、零真实 API（pytest-socket 已全局断网）。

## 1. 必读（权威源，按顺序）

1. `.scratch/m5-remediation-gates/issues/03-proposal-persistence-state-machine.md` —— **本票权威**，验收 7 条逐条打勾。
2. `docs/design/m5-remediation-gates-design.md`：
   - §G2 确认门会话语义（D-40：confirm 即执行，approve 同步走执行+验证）
   - §G8 留痕落点（第八表 remediation_proposals，显式偏差已评审）
   - **数据模型变更节**（remediation_proposals 全字段表 + incidents 状态流转注 + evidence_steps/investigations 无变更）
   - 架构依赖线 §C3（api→remediation→db 单向合法）
   - T3 行（spec 表格同源）
3. `docs/design/decisions.md`：**D-46**（第八表字段权威）/ D-40（confirm 即执行、提案不自动过期）/ D-30（现库加表 create_all 幂等）/ D-19（incidents 五字段冻结）/ D-25（evidence_steps 冻结列）/ D-31（investigations 冻结）。**本票不新增 D 编号**；实现中发现 D-46 字段缺漏需微调 → 停手问用户走评审，不自行改决策。
4. `CONTEXT.md`：处置提案词条（含状态集 pending→approved/rejected→executing→recovered/failed/rolled_back/escalated）——命名照抄。
5. `docs/architecture/architecture.md` §4 数据模型：现列 7 行清单（scenarios/alert_events/incidents/investigations/evidence_steps/hypotheses/eval_runs），标题「六张核心表」已是过时值（M4 遗留未修）；本票增补第 8 行 + 顺手把标题改为「八张核心表」。
6. 代码先例（实现前必读，按此顺序）：
   - `src/oncall/db/models.py`（202 行）——Base / CheckConstraint / @validates / mapped_column 风格；第八表照 Investigation / EvidenceStep 加列
   - `src/oncall/db/__init__.py` —— `create_tables = Base.metadata.create_all` 幂等（第八表自动纳入，无需改）
   - `src/oncall/harness/tools/execute.py`（issue 02 产物）—— **ProposalCreator 接缝形状** `(payload: {runbook_slug, action_id, dry_run_json}) -> str`；本票 service 层填这个接缝（只提供实现，不改 execute.py）
   - `src/oncall/ingest/service.py` —— 函数式服务先例：`ingest_webhook(session, ...)`、dataclass 结果、`if TYPE_CHECKING: Session`
   - `tests/unit/test_db_evidence_models.py` + `tests/unit/test_db_evidence_repo.py` —— **DB 测试装配先例**：`create_engine("sqlite://")` + SQLite PRAGMA foreign_keys（event listener）+ `create_tables(engine)` + `with Session(engine)`；列集合与文档逐字段断言、create_all 二次幂等、FK 拒绝、CHECK 拒绝的组织方式
   - `docs/design/m4-evidence-chain-design.md` §数据模型变更 —— 第七表先例（列集合测试 + D-25「文档是权威」纪律）

## 2. 任务（交付物）

### A. `src/oncall/db/models.py` 增 `RemediationProposal`（第八表，≈45 行 → 文件 ≈247 < C6 300）

- 列**逐字段照 D-46 / 设计数据模型节**，不擅改列名（D-25 同款纪律）；建议 nullable 映射（实现票定并回填）：
  - `id` PK autoincrement
  - `incident_id` FK→incidents.id **NOT NULL**（锚 incident；一次事故可多次处置尝试 → **不加唯一约束**，加 index 供 `GET /remediations?incident_id=`）
  - `investigation_id` FK→investigations.id **nullable**（无产出调查的处置可建行，验收 #4）
  - `runbook_slug` / `action_id` String NOT NULL
  - `status` String(16) NOT NULL default `'pending'` + **CheckConstraint 值集八值**（照 ck_ 先例，脏值进不来）：`pending/approved/rejected/executing/recovered/failed/rolled_back/escalated`
  - `dry_run_json` JSON **NOT NULL**（批准对象——创建即有，验收 #5 全路径不可变）
  - `params_json` JSON nullable（执行参数/上下文，05/06 写；本票保证可落可读，service create 接受可选参数）
  - `decision` String nullable（confirm 时落 approve/reject；可加 CHECK `IN ('approve','reject') OR NULL`，可选）
  - `confirm_reason` Text nullable
  - `confirmed_at` / `executed_at` / `finished_at` DateTime(timezone=True) nullable
  - `verify_result_json` JSON nullable（06 写）
  - `rollback_status` String nullable（06 写）
  - `created_at` NOT NULL default `lambda: datetime.now(UTC)`（照既有表）
- 若 models.py 超 300 行拆 `models_remediation.py`（同包无新边，票面行 21 预案；预计不超）。
- 不扩 incidents / investigations / evidence_steps / hypotheses 任何冻结列（D-19/D-25/D-31）——只新增一个类。

### B. `src/oncall/remediation/service.py`（新文件，预算 ≈260 < C6 300）——处置状态机，**无 LLM 确定性核心**

- 状态集 + **合法迁移表**以模块常量钉死。C8 合规：`UPPER_CASE: Final[...] = {...}`（AnnAssign 形态，照 permission.py `LEVEL_DECISIONS` 先例——C8 只扫小写名普通 Assign；勿写裸 `transitions = {...}`）。
- **迁移合法性集中一处**：单一校验（当前态 → 目标态是否合法），非法迁移抛领域错误（如 `ProposalStateError`），终态不可再迁移；每迁移方法薄封装。
- 函数式服务（ingest/service.py 先例，`session: Session` 首参注入）：create / get / list_by_incident + 迁移方法（approve、reject、start_execution、complete_* 或 mark_recovered 等）。
- `create_proposal(session, *, incident_id, runbook_slug, action_id, dry_run_json, params_json=None, investigation_id=None)` → 返回 proposal id；`investigation_id` 默认 None（验收 #4）。
- 服务方法只写 `status / decision / confirm_reason / confirmed_at / executed_at / finished_at / verify_result_json / rollback_status`；**永不写 `dry_run_json`**（批准对象不可变，验收 #5——全路径测试钉死）。
- **暴露与 issue 02 `ProposalCreator` 接缝对齐的工厂**（供 04 装配注入）：`make_proposal_creator(session, incident_id) -> Callable[[dict], str]` —— payload 3 键（runbook_slug/action_id/dry_run_json）落 pending 行返回 id。**C3 零边**：remediation 不 import harness（接缝是结构协议，鸭子类型即满足）。
- 时间戳口径（实现票定并回填）：confirmed_at 在 approve/reject 落、executed_at 在 executing 落、finished_at 在终态落。

### C. 导出

- `src/oncall/remediation/__init__.py` 按需补导出（service 函数/错误类型）；`db/__init__.py` **不扩**（现仅 AlertEvent/Incident 旧导出面，测试直接 `from oncall.db.models import RemediationProposal`）。

### D. 测试（TDD 红绿，本票两个测试文件）

- `tests/unit/test_db_remediation_proposals.py`（建表层）：
  - 列集合与 D-46 逐字段一致；create_all 二次幂等
  - FK 拒绝（非法 incident_id / investigation_id）
  - status CHECK 拒绝非法值
  - investigation_id NULL 可建行（验收 #4）；同一 incident 可多行（一对多，不加唯一）
  - incident_id 关联正确
- `tests/unit/test_remediation_service.py`（状态机 + 接缝）：
  - 合法迁移全链通过（pending→approved→executing→recovered 及 rejected/rolled_back/escalated 分叉——**迁移表你定稿**）
  - 非法迁移拒绝：pending→recovered 直跳、终态再迁移、executing→rejected 等，报错清晰
  - `dry_run_json` **全路径不可变**：走完一条合法链后重新读行，dry_run_json deep-equal 原文（验收 #5）
  - proposal 行字段全落：dry_run_json 原文、params_json、decision/confirm_reason、时间戳（验收 #3）
  - `make_proposal_creator` 与 issue 02 接缝形状对账：payload 3 键 → 返回 id → 可回读 status=pending 行
  - investigation_id 可空路径（验收 #4）

### E. 回写（随本票同一个 feature commit）

- `docs/architecture/architecture.md` §4：数据表清单增补第八表 remediation_proposals 行（含字段摘要 + `← M5 增补第八表（2026-09-09，G8/D-46）` 注记）；标题「六张核心表」→「八张核心表」（顺手修 M4 遗留计数不一致）。
- issue 03 票：`Status: ready-for-agent → resolved`、验收 7 条打勾、落位注记回填（表最终列、迁移表定稿、approved 语义处理、架构 §4 回写 commit）、修正第 7 条旧基线数字为实际。
- `.scratch/m5-remediation-gates/spec.md`：03 行就绪态 → `resolved`。
- CONTEXT：本票如无新共享概念不加（处置提案词条已含状态集）；确需新增当场入表（硬规 12）。
- decisions.md：不新增 D；无评审级变化则不动。

## 3. 边界（明确不做）

- ❌ 不建 confirm/reject API 端点、不碰 POST /investigate 的 remediation 节装配（**归 04**）；不实装白名单/受控执行器 subprocess（**归 05**）；不实装 PromQL 回查 verifier / 回滚驱动 / incident 翻 mitigated（**归 06**）。本票只把状态集 + 合法迁移边一次定全，供 05/06 驱动。
- ❌ 不把真实 proposal_creator 注入 loop 的 execute_action 装配点、不改 `execute.py` / `registry.py` / `permission.py` / 任何 harness 文件（issue 02 已入库，行为测试零改动）——只提供 service 层实现 + 接缝工厂，装配归 04。
- ❌ 不翻 `incidents.status`（mitigated 是既有枚举值，本票不扩列不扩枚举——D-19 注记仅背景）。
- ❌ 零 LLM / 零 HTTP / 零 subprocess / 零新依赖（remediation 包纪律；SQLAlchemy 已有）。`service.py` 只 import `oncall.db.models` + sqlalchemy，**不 import harness**。
- ❌ 不把 `.workbuddy/`、`.scratch/tmp/` 卷进 commit。

## 4. 已知裁决与踩坑（预铺，实现时留痕）

1. **approved 态语义（易混淆点）**：D-40「confirm 即执行」= 04 在**同一 confirm 调用内**同步遍历 pending→approved→executing→终态；approved 是「人点头已落库」的持久标记（decision/confirm_reason/confirmed_at 落点），同步流里即使瞬态**也必须**是合法态。迁移表须同时服务：04 同步链、05/06 分段驱动、回滚路径。06 语义（D-44/D-45）参考：恢复验证通过 → recovered；未恢复 → 执行 rollback（slow-sql 回滚 = KILL 幂等重试；cpu-spike rollback=[] 即无回滚）→ 仍失败 → escalated（incident 保持 investigating）；**回滚为空的 runbook 未恢复需有直转 escalated 的合法边**。迁移表终稿回填落位注记；若与 06 后续期望冲突，06 开工前会核对本表。
2. **基线数字**：票面「541/10」是 M3 末期旧值；实测当前 580/10。实现后按实际（580 + 本票新增）更新票面该行。
3. **C8 踩坑**：迁移表/状态集常量用 `UPPER: Final[...] = {...}`（AnnAssign），勿写裸小写 `= {...}`。
4. **分层纪律**：DB CHECK 只锁 status 值集（照 ck_ 先例）；迁移规则只在 service 层——勿把迁移表塞进 models.py。
5. **models.py 行数**：现 202 行 + 第八表 ≈45 → ≈247 < 300 不必拆（超限才拆 models_remediation.py）。
6. **dry_run_json 内容形状 = issue 02 定稿**：`{runbook_slug, action_id, action_name, commands:[{step, action, command, impact, runtime_params}], impact}`；测试 fixture 照此构造；验收「原文落库」= 读出 deep-equal。
7. **service 命名与形状**：函数式 + `Session` 注入（ingest/service.py 先例）；返回/错误类型命名复用 CONTEXT 词，新领域错误（如 ProposalStateError）若入 CONTEXT 当场入表。
8. **DB 测试装配**：in-memory sqlite + `PRAGMA foreign_keys=ON`（event listener，FK 拒绝断言才生效，照 test_db_evidence_repo.py）+ `create_tables(engine)`。
9. 提交粒度：**一个 feature commit**（`feat(M5-处置闸门): ...`，scope 与 issue 01/02 一致）含代码 + 测试 + 架构回写 + 票面更新；body 写为什么；`Closes .scratch/m5-remediation-gates/issues/03-proposal-persistence-state-machine.md`。不跳过 hooks。

## 5. 验证清单（门禁，逐条过）

- [ ] TDD：先写测试跑红 → 实现跑绿（本票两个测试文件）
- [ ] 全量 `python -m pytest tests`：`580 passed / 10 skipped` → 只增不回退
- [ ] `ruff check .` 与 `ruff format --check .` 全绿
- [ ] 架构守卫 `pytest tests/test_architecture_guards.py` 7 passed（C3 remediation→db 合法、C6 models.py/service.py ≤300、C8 无小写模块级可变全局，新文件被扫）
- [ ] issue 03 验收 7 条逐条打勾
- [ ] 收尾汇报给用户确认后入库：迁移表定稿（含终态/回滚直边）、approved 态语义处理口径、架构 §4 回写位置与标题修正、验收计数实际值、是否需要用户注意的裁决点
