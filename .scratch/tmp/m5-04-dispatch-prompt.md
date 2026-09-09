# M5 issue 04 派工 prompt — 确认门 API（T4 / G2 / D-40）

> 新开会话执行本票。prompt 完整自洽：含环境、TDD、门禁、设计引用与验收标准，照 issue 01–03 惯例。
> 执行完把「端点最终契约、终态返回形状、降级路径实测、验收计数实际值」列给用户确认后再入库。

## 0. 环境与仓库基线

- 仓库：`F:\Git repository/oncall-copilot`（Windows 11 / Git Bash；文件与解释器一律绝对路径）
- Python：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（装包走清华源；本票零新依赖——FastAPI/TestClient 已有，无需装包）
- **基线 HEAD：`ecd6e99`**（前序：`369a176` issue 01 → `a2d1369` issue 02 → `914fdc6` worklog → `ecd6e99` issue 03）。开工先 `git status` 确认工作区干净（仅 `.workbuddy/` 日志与 `.scratch/tmp/` 派工 prompt 属例外，**勿动、勿入库**）。
- 前置依赖已满足：issue 03（proposal 第八表落库 + 处置状态机 + `make_proposal_creator` 接缝工厂）**已入库** → 本票 `Blocked by: 03` 解除。
- **门禁基线实测：`609 passed / 10 skipped`**（issue 03 入库后真值）。⚠️ 票面验收第 8 条写的「基线 541/10」是 M3 末期旧值，以 609/10 为准；实现后按实际数字更新票面该行。
- 硬规：AGENTS.md 12 条 + commit 规范（中文 + type 前缀、body 写为什么、Closes 用 issue 文件路径）；术语一律用 CONTEXT.md 词（处置提案/干跑预览/恢复判据/受控执行/推理与执行分离已入表）；TDD 红绿循环；测试只住约定接缝上；零 LLM、零真实 API（pytest-socket 已全局断网；TestClient 走进程内 ASGI 属既有豁免先例，照 test_ingest_api.py）。

## 1. 必读（权威源，按顺序）

1. `.scratch/m5-remediation-gates/issues/04-confirmation-gate-api.md` —— **本票权威**，验收 8 条逐条打勾。
2. `docs/design/m5-remediation-gates-design.md`：
   - **§G2 表行（约 154 行）**：confirm 即执行（approve 同步走受控执行 + 恢复验证，≤5min 预算返回终态）；**提案不做自动过期**（pending 期 incident 保持 investigating，人工可随时 confirm/reject，超时不熔断提案——D-28「转人工不是丢弃」）
   - **§API 契约表（约 122–124 行）**：三个端点的形状权威——`POST /remediations/{proposal_id}/confirm`（body `{decision: "approve"|"reject", reason?: str}`）、`GET /remediations/{proposal_id}`、`GET /remediations?incident_id=`
   - 架构依赖线（约 104–105 行）：**api → remediation → db 单向合法**（与 api→classify→db 同构）；harness → remediation 零静态依赖
   - T4 行（spec 表格同源）
3. `docs/design/decisions.md`：**D-40**（confirm 即执行、提案不自动过期）/ **D-48**（降级形态：不注入受控执行器 = 裁剪达成路径——confirm 落「建议已确认，执行能力未配置」503 + proposal 留 approved 即终，只砍注入面不改契约）/ D-46（第八表字段）/ D-44/D-45（06 语义：恢复验证判据与回滚来源——本票只注入替身，不实装）。**本票不新增 D 编号**；实现中发现契约缺漏需微调 → 停手问用户走评审，不自行改决策。
4. `CONTEXT.md`：处置提案词条（状态集 + dry_run_json 批准对象语义）——命名照抄。
5. 代码先例（实现前必读，按此顺序）：
   - `src/oncall/remediation/service.py`（issue 03 产物）—— **状态机消费面**：`approve/reject/start_execution/mark_recovered/...` 迁移方法、`_TRANSITIONS` 迁移表、`ProposalStateError`、`get_proposal/list_by_incident`；API 层只编排这些函数，**不在 api 层重写状态逻辑**
   - `src/oncall/api/investigation.py`（293 行）—— **router + deps 注入先例**：`create_investigation_router(engine, deps) -> APIRouter`、Pydantic 请求体、HTTPException 状态码语义
   - `src/oncall/api/routes.py` —— router 挂载组织（`create_remediation_router` 照此挂入 app）
   - `tests/unit/test_investigation_api.py` + `tests/unit/test_ingest_api.py` —— **API 测试装配先例**：内存库 + StaticPool（跨线程共享同一内存连接）+ `TestClient`（进程内 ASGI，pytest-socket 豁免注释）；4xx 断言组织方式
   - `src/oncall/harness/tools/execute.py` —— **Protocol 接缝先例**（issue 02）：注入面用结构协议 + 鸭子类型，api/remediation 不新增对 harness 的依赖边

## 2. 任务（交付物）

### A. `src/oncall/api/remediation.py`（新文件，预算 ≈140 行，C6 ≤300）

- `create_remediation_router(engine, deps) -> APIRouter`（照 investigation.py 形状），挂入 app 组装点。
- **三个端点**：
  - `POST /remediations/{proposal_id}/confirm`：body `{decision: "approve"|"reject", reason?: str}`（Pydantic 模型，decision 字面量约束 → FastAPI 自动 422）
    - **approve 同步执行（D-40）**：`service.approve`（approved 落 decision/reason/confirmed_at）→ 经注入的受控执行器替身执行 dry_run_json 命令清单 → `start_execution` → 经注入的恢复验证器替身回查 → `mark_recovered/mark_failed/...` → **返回终态行**（demo 秒级，测试内同步完成）
    - reject：`service.reject` 落 rejected + reason 留痕，返回行
    - 4xx 语义：不存在 **404**；已终态再 confirm **409**（`ProposalStateError` 捕获后映射，api 层不重写状态判断）；decision 非法 **422**（Pydantic 自动）
    - **降级路径（D-48）**：deps 未注入执行器时 approve → 503 + 说明 body + proposal 留 approved 即终（不回滚 approved 态——建议已确认是可审计事实）
  - `GET /remediations/{proposal_id}`：返回干跑预览/状态/确认理由/执行输出摘要/验证结果/回滚状态（读表序列化，无 UI 阶段处置留痕载体）；不存在 404
  - `GET /remediations?incident_id=`：返回该事件全部处置（一对多含多次尝试，按 id 序）
- **注入面**：`RemediationExecutor` + `RecoveryVerifier` 两个 Protocol 接缝（结构协议，照 issue 02 先例；**建议住 remediation 侧**——如 `src/oncall/remediation/executor.py` 或 service.py 内，实现票定并回填；api 只经 deps 拿实例）。本票不实装，测试用替身（调用断言）。
- **approve 后 dry_run_json 原样作为执行清单**（验收⑥）：执行器收到的命令清单 deep-equal 行内 dry_run_json——批准对象锁定，api 层也不改写。

### B. 测试（TDD 红绿，一个测试文件）

- `tests/unit/test_remediation_api.py`：
  - 装配照 test_investigation_api.py：内存 sqlite + PRAGMA foreign_keys + create_tables + StaticPool + TestClient
  - confirm approve → 注入替身执行器/验证器**调用断言**（收到的命令清单 = dry_run_json 原文）→ 同步返回终态行
  - confirm reject → proposal 落 rejected + reason/decision/confirmed_at 落库
  - GET 单个 / GET by incident（多次尝试两行都在）
  - 4xx：不存在 404、已终态再 confirm 409、decision 非法 422
  - 降级（D-48）：未注入执行器 approve → 503 + proposal 留 approved
  - dry_run_json 全路径不可变在 API 层复验（走完 confirm 后重读 deep-equal）

### C. 回写（随本票同一个 feature commit）

- issue 04 票：`Status: ready-for-agent → resolved`、验收 8 条打勾、落位注记回填（端点最终契约、终态返回形状、注入面落位、降级路径实测、基线数字修正为实际）。
- `.scratch/m5-remediation-gates/spec.md`：04 行就绪态 → `resolved`。
- `docs/architecture/architecture.md`：若 §3/§5 有 API 清单且含 remediation 端点缺口则顺手补（实现票判断，不确定就问）。
- CONTEXT：无新共享概念不加；若 Protocol 名（如受控执行器/恢复验证器）成共享词汇当场入表（硬规 12）。
- decisions.md：不新增 D；无评审级变化则不动。

## 3. 边界（明确不做）

- ❌ **不实装**受控执行器 subprocess/白名单（归 05）、PromQL 回查 verifier/回滚驱动/incident 翻 mitigated（归 06）——本票只有 Protocol 接缝 + 测试替身。
- ❌ 不把真实 proposal_creator 注入 loop 的 execute_action 装配点、不改 `execute.py`/`registry.py`/`permission.py`/任何 harness 文件（装配归后续票统一收口）。
- ❌ 不做 M8 UI、不做提案过期/定时熔断（D-40 提案不自动过期）。
- ❌ 零 LLM / 零真实 HTTP 外呼（TestClient 进程内豁免照旧）/ 零新依赖。
- ❌ 不把 `.workbuddy/`、`.scratch/tmp/` 卷进 commit。

## 4. 已知裁决与踩坑（预铺，实现时留痕）

1. **confirm 即执行的同步语义**：approve → approved（持久落点）→ executing → 终态在一次请求内完成；测试替身执行/验证即时返回即可，不引后台任务。503 降级时 **approved 是合法停留态**（不是中间态遗留 bug）。
2. **状态判断只在 remediation 层**：api 层捕获 `ProposalStateError` 映射 409，不自行查表判断状态（单一权威，别复制迁移逻辑）。
3. **基线数字**：票面「541/10」是 M3 末期旧值；实测当前 609/10。实现后按实际（609 + 本票新增）更新票面该行。
4. **C8 踩坑**：模块级常量用 `UPPER: Final[...]`（AnnAssign），勿写裸小写 `= ...`。
5. **同文件并行 Edit 会丢改动**（issue 03 实测重演）：同一文件多次编辑必须串行，改完 grep 复核。
6. **API 测试装配**：内存库 + StaticPool 是 TestClient 线程模型下共享连接的关键，漏了会偶发 "no such table"；照 test_investigation_api.py 逐行抄。
7. **proposal 行测试数据构造**：用 `create_proposal` service 函数建行（别手写 ORM 绕过 service），与 issue 03 测试先例一致。
8. 提交粒度：**一个 feature commit**（`feat(M5-处置闸门): ...`，scope 与 issue 01–03 一致）含代码 + 测试 + 票面更新；body 写为什么；`Closes .scratch/m5-remediation-gates/issues/04-confirmation-gate-api.md`。不跳过 hooks。

## 5. 验证清单（门禁，逐条过）

- [ ] TDD：先写测试跑红 → 实现跑绿
- [ ] 全量 `python -m pytest tests`：`609 passed / 10 skipped` → 只增不回退
- [ ] `ruff check .` 与 `ruff format --check .` 全绿
- [ ] 架构守卫 `pytest tests/test_architecture_guards.py` 7 passed（C3 api→remediation 合法、remediation 零 import harness、C6 新文件 ≤300、C8）
- [ ] issue 04 验收 8 条逐条打勾
- [ ] 收尾汇报给用户确认后入库：端点最终契约、终态返回形状、注入面落位、降级路径实测、验收计数实际值
