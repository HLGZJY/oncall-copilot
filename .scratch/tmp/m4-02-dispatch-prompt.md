# M4-02 派工 prompt：证据仓库写入接缝（T2 / G4–G5；D-33/D-34/D-25）

oncall-copilot M4「证据链与过程存储」第 2 票：新增 `src/oncall/db/evidence_repo.py`，
把 M3 调查循环的内存契约（`InvestigationSession`/`EvidenceStep`/`Hypothesis`）**步进即写**
落库到 M4-01 已建的 `investigations` + `evidence_steps` + `hypotheses` 三表。
**严格 TDD；零 LLM 真实调用、零 HTTP；不碰报告读库（注册表换读库归 03 票，`GET /investigations/{incident_id}` 本票不动）。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`796e79e`**（M4-01 ORM 三表已落位），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests --tb=no -p no:warnings -q`（当前 **494 passed / 7 skipped，coverage 98%**）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest 输出用 `> file; PYEXIT=$?` 取退出码（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key；测试一律内存 SQLite（**勿写坏仓库根的现库 `oncall.db` 文件**）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：证据仓库 / Evidence Repository、调查记录 / Investigation Record、证据步 / Evidence Step、假设（confirmed/rejected/active）、调查会话 / Investigation Session；本票**无新术语**，若确需造词当场入表并写 `_Avoid_`）
3. `.scratch/m4-evidence-chain/issues/02-evidence-repo-write-seam.md`（**权威票面**，下方为摘要）+ `.scratch/m4-evidence-chain/spec.md`「关键契约」节
4. `docs/design/m4-evidence-chain-design.md`（**权威设计，status: reviewed**）——重点：§技术方案一句话概括、§C3/C6 论证（落库接缝落 `db` 层的论证）、§数据模型变更、开放点 G4/G5 定案、§开发计划 T2 行
5. `docs/design/decisions.md`：**D-33（步进即写，写失败不静默吞、冒泡归类 tool_error）、D-34（成本会话级汇总 = 收尾结构 total 两字段落库即取，步级 tokens/cost 维持工具埋点语义、不做步级摊销）、D-25（M3/M4 边界：内存契约不倒改、行 id 替换指针接口不变）**；冻结面 D-28（终止语义与 escalate 落点）、D-19
6. 代码（行号以基线 `796e79e` 为准）：
   - `src/oncall/harness/session.py` 全文（内存契约：`record_step` 步号连续校验、`_ensure_running`、conclude/escalate/abort）
   - `src/oncall/harness/loop.py`——重点：`LoopComponents`（57–64，frozen dataclass）、`_escalate`（107–110）、`_execute_step`（174–224，**record_step 在 198–211**）、`_update_hypothesis`（227–248，**add_hypothesis 在 240**）、abort 调用点（215、261）、`_handle_conclusion`（251–279，conclude 278）、`_build_result`（282–297，InvestigationResult.total_* 现口径 = 步级求和）
   - `src/oncall/api/investigation.py` 121–154（run_investigation 接线点 144–147；`InvestigationSession(incident_id=...)` 每次新建在 145 行）
   - `src/oncall/db/models.py`（M4-01 三表契约：`Investigation`/`EvidenceStep`/`Hypothesis`、incident_id 唯一约束、`uq_evidence_steps_incident_step`、CHECK）
7. 测试基建先例：`tests/unit/test_db_evidence_models.py`（**engine fixture 带 `PRAGMA foreign_keys=ON` 事件监听——本票 FK/约束断言照此模式**）；`tests/unit/test_harness_loop.py` 的 `make_components` helper（`LoopComponents` 构造点，加字段后需同步）；`tests/unit/` 不是包，裸 import conftest；autouse 断网 fixture 天然满足本票

## 2. 任务（权威票面 = issue 02，下方为摘要）

落位：新增 `src/oncall/db/evidence_repo.py`（**行数预算 ≈150，< C6 300**）+ 新增 `tests/unit/test_db_evidence_repo.py` + **最小接线**（`loop.py` / `api/investigation.py`，见 §3）。

- **步进即写（D-33）**：`record_step` / `add_hypothesis` / 终态方法（conclude/escalate/abort）与 DB 写在**同一点**，每步一个事务；接缝经**注入**传入 loop/api（`LoopComponents` 增 repo 字段，**缺省 None 不写库**——既有 loop 单测不接库零回退）
- **行 id 回填（D-25）**：内存契约不倒改；行 id 在接缝层映射（session↔row 对账表，封装进 repo 实例状态，C8 禁模块级可变全局），`[truncated, full at step N]` 指针接口不变
- **收尾写 `investigations` 行**：终态/stop_reason/conclusion/failure_mode/step_count + `total_tokens`/`total_cost_cny`（**D-34：落库即取 `InvestigationResult.total_*` 两字段现口径**，不改收尾结构；escalated/aborted 路径同样要走到收尾写行）
- **写失败不静默吞（D-33）**：落库异常按 Harness 熔断语义冒泡归类 `tool_error`；**写失败时该步不得进入 session**（避免内存/库双头不一致）——即**先 DB 写成功、再 `session.record_step` 入内存**，DB 失败抛异常走熔断，session 原样（此顺序语义用测试钉死）
- **覆盖语义**：同 incident 二次调查 → `investigations` 行 upsert 覆盖（incident_id 唯一约束）+ 该 incident 旧 `evidence_steps`/`hypotheses` 行同步清理，保证「库内 = 最近一次调查」（对齐现注册表覆盖语义）；对账表旧映射一并清理
- repo 方法粒度建议三类：`record_step` / `add_hypothesis` / `finalize`（收尾行），加 `begin`（调查开局建 investigations running 行 + 处理覆盖清理）——以票面验收为准自行裁决，裁决写入提交 body

TDD 顺序建议：3 步调查逐行落库与 session.steps 逐字段一致（步进即写非批量）→ escalated 与 aborted 已取证部分完整 + investigations 终态行正确 → 行 id 回填后指针接口断言不变（D-25 键集合不破）→ 落库异常注入 → 熔断归类 `tool_error`、无静默、该步未进 session → 同 incident 二次调查覆盖旧行、库内只剩最近一次 → 全量门禁。

## 3. 边界（勿越）

- 只新增 `src/oncall/db/evidence_repo.py` + `tests/unit/test_db_evidence_repo.py`；生产代码只做**最小接线**：
  - `loop.py`：`LoopComponents` 加 `evidence` 字段（缺省 None 不写库）+ 在 record_step/add_hypothesis/终态点接 repo 调用
  - `api/investigation.py`：组装 repo（engine 已在 create_investigation_router 入参里）并传入 components
  - **不改 `session.py` 内存契约、不改 `models.py`、不改 `views.py`、不碰 `GET /investigations` 与注册表（归 03）、不碰 report.md（归 04）**
- `loop.py` 现 297 行贴 C6——本票对 loop.py 净增尽量小（repo 调用收进短辅助函数），**view/opening 重构归 05 票，勿顺手重构**
- C3：`oncall.db` 不在 harness 禁列（pyproject `[tool.importlinter]` C3 契约），harness → db 单向 import 合法；repo 不 import `oncall.api`/`classify` 等业务包
- 零 LLM 真实调用、零 HTTP；`datasets/golden/holdout/` 禁读
- ruff：`max-args=5`、PLR0912 分支 ≤12、PLR0913 参数 ≤5、函数 ≤50 语句；C6 单文件 ≤300 行；C8 模块级可变全局禁用
- 提交规范：中文 + type 前缀，预期 `feat(M4-证据链): ...`；body 写**为什么**（含 repo 方法粒度裁决理由）；引用 `.scratch/m4-evidence-chain/issues/02-evidence-repo-write-seam.md`；不跳过 hooks

## 4. 已知踩坑（M0–M4 实录）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘（loop.py 多点接线时尤其注意）
- ② ruff：`max-args=5`、PLR0912 ≤12、PLR0913 ≤5、函数 ≤50 语句
- ③ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture
- ④ SQLite FK 默认不强制——测试 engine 需 `PRAGMA foreign_keys=ON` 事件监听（照 `test_db_evidence_models.py` 先例，直接抄）
- ⑤ StrEnum 值小写串：`SessionStatus`（running/concluded/escalated/aborted）、`HypothesisStatus`（confirmed/rejected/active）落库前统一 `str()` 归一，保证与 CHECK 约束值一致
- ⑥ pytest 输出统计行用重定向 + 退出码取，别用管道 grep 吞退出码
- ⑦ `LoopComponents` 是 **frozen dataclass**——新字段给默认值（`evidence: EvidenceRepository | None = None`），`make_components` 构造点与既有单测即零改动或仅 helper 一行同步
- ⑧ **ORM 类与内存契约同名**（`db.models.EvidenceStep` vs `harness.session.EvidenceStep`）——evidence_repo 同文件 import 时给 ORM 侧用别名（如 `as EvidenceStepRow`），防混
- ⑨ 会话每次新建（investigation.py 145 行）——对账表键建议用 `id(session)` 或（incident_id + started_at），覆盖调查时务必同步清旧映射，防陈旧行 id 回填错步
- ⑩ 先库后内存的顺序（§2 第 4 条）必须用测试钉死：注入写失败后断言 session.steps 长度不变 + 熔断归类 tool_error

## 5. 验证路径（收尾清单）

- [ ] issue 02 验收六条逐项打勾（步进即写逐行一致 / escalated+aborted 完整落库 / 行 id 指针接口不变 / 写失败熔断 tool_error 无静默 / 二次调查覆盖清理 / 全量门禁），在 issue 文件内回填注记
- [ ] 架构守卫全绿：`tests/test_architecture_guards.py` 全绿（C3/C4/C5/C6/A2）
- [ ] 门禁：pytest（基线 **494 passed / 7 skipped 只增不减**，coverage ≥98%）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；`.scratch/m4-evidence-chain/spec.md` 任务序列表 02 行勾选
- [ ] 收尾汇报：落位文件清单 + 测试计数 + 步进即写/覆盖语义/先库后内存三点的实现说明 + 下一票（03 报告读库与 JSON 定案，blocked by 02 已解除）就绪确认
