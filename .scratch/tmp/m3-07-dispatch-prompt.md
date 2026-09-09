# M3-07 派工 prompt：调查入口 API——POST /investigate + GET 报告（T7）

oncall-copilot M3「自主根因调查循环」第 7 票：调查入口与报告查询——`POST /investigate {incident_id}` 同步执行 M3 主循环（T6 已落的 `run_investigation`），`GET /investigations/{incident_id}` 从**进程内报告注册表**读最近一次调查报告（M4 落库后换读表）。**落 `src/oncall/api/`（组装点注入 harness 组件，api → harness 方向不在 C3 禁列）；严格 TDD，本票零 LLM 真实调用、零 HTTP 外呼（MockPlanner + MockVerifierJudge + 假工具 handler 编排，契约测试照 M2 `test_classify_api.py` / `test_alert_card_api.py` 先例走 `inproc_asgi` + TestClient + 键集合精确匹配）。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`83781a3`**（M3-06 已落主循环 Loop），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests --cov=src/oncall`（当前 **450 passed / 4 skipped，coverage 97.59%**，C9 门槛 80%）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest addopts 已带 `-q`，命令行勿再叠 `-q`；要留退出码用 `> file; PYEXIT=$?`
- API 测试带 `pytestmark = pytest.mark.inproc_asgi`（进程内 ASGI 传输，无真实网络 IO，A1 断网不适用——照 `test_classify_api.py` 先例）
- 不需要起容器栈；不需要任何 LLM API key
- **允许改动**：`src/oncall/api/routes.py`（125 行，有充足余量）与 `src/oncall/ingest/app.py`（组装点）、`src/oncall/api/` 下新建文件；**不改 harness 任何文件**（`loop.py` 297 行贴 C6 上限——发现必须改 harness 的，停手在 issue 注记记录）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：调查会话 / 事件 / 证据步 / 假设 / 决策输出 / 失败模式 / 转人工 / **调查收尾结构 / Investigation Result**；新术语当场入表并写 `_Avoid_`）
3. `.scratch/m3-investigation-loop/issues/07-investigate-api.md`（**权威票面**，下方为摘要）
4. `docs/design/m3-investigation-loop-design.md`：§API 变更表（`POST /investigate` / `GET /investigations/{id}` 两行——入参、404 语义、响应要点、零写操作）+ §开发计划 **T7 行**（验收口径）+ §风险清单第 7 行（同步 5min 占用——v1 单进程语义，后台任务化不在 M3）
5. `docs/architecture/agent-loop-design.md`：§「证据链数据形状」（报告 JSON 形状权威：steps/hypotheses/conclusion/confidence，M4 数据形状可直接用）
6. `docs/design/decisions.md`：**D-28（escalate 无 UI 落点 = 状态 + 可查报告——本票是「转人工」出口的实现载体）**、D-25（不建表）、D-19（incidents 五字段，`status` 枚举 `deduped→classified` 冻结不扩——incident 调查中状态语义见票面要点）
7. **代码先例（照抄结构，不抄业务）**：
   - `src/oncall/api/routes.py`（`create_router` 组件注入先例：缺省依赖落 503 不静默降级；`ClassifyRequest` Pydantic extra=forbid 入参契约）
   - `src/oncall/api/card.py`（`build_alert_card`——D-17 卡片组装，时间锚 `last_fired_at`；`list_incidents`——incidents 查询面）
   - `src/oncall/ingest/app.py`（`create_app` 组装点：engine/依赖注入 + `create_router` 挂载先例）
   - `src/oncall/harness/loop.py`（T6：`run_investigation(session, LoopComponents)` 返回 `InvestigationResult`——**API 只消费，不改**）
   - `src/oncall/harness/session.py`（`InvestigationSession(incident_id=...)` 构造 + 序列化）
   - `tests/unit/test_harness_loop.py`（MockPlanner 剧本 + `make_components`/假 handler 夹具——**整套搬进 API 契约测试复用**）
   - `tests/unit/test_classify_api.py`（inproc_asgi 标记 + TestClient + create_app 组装 + 键集合精确匹配先例）；`tests/unit/test_alert_card_api.py`（D-17 键集合守卫同法）
8. `docs/agents/issue-tracker.md`（issue 状态流转约定）

## 2. 任务（权威票面 = `.scratch/m3-investigation-loop/issues/07-investigate-api.md`，下方为摘要）

### `POST /investigate`

- 入参 `{incident_id}`（Pydantic extra=forbid）；incident 不存在 → **404**
- 开局锚点：以该 incident 的 **`alert_ids[0]`** 调 `build_alert_card` 取 D-17 事件卡片作为调查开局上下文（时间锚 `last_fired_at`）——单测钉死锚点行为
- 同步 v1：注入 harness 组件（planner / registry / gate / verifier / clock 全部可从 `create_app`/`create_router` 注入，缺省语义自行定义并单测——建议照 `classify_runtime` 先例：缺省落 503 不静默降级到 mock）执行 `run_investigation`
- 响应 = `InvestigationResult` 序列化（conclusion / **termination**（= session 终态）/ **failure_mode** / steps / hypotheses / **成本汇总** / confidence）；报告 JSON 形状照 agent-loop-design §证据链数据形状（steps/hypotheses/conclusion/confidence 键集合精确匹配守卫）
- **零写操作**（execute_action 是 L2 stub），四道闸门不适用

### `GET /investigations/{incident_id}`

- 从**进程内报告注册表**读最近一次调查报告；无记录 → **404**
- **escalated 报告同经此出口**（G7：无 UI 阶段「转人工」落点 = escalated 状态 + 证据链可查）——单测钉死
- 注册表语义：dev 单进程；同 incident 重复调查**覆盖旧报告**（容量防御）；M4 落库后替换为读表（代码注释注明）

### 语义定义（票面要点，须单测）

- incident 存在但 status ≠ investigating：**建议仍允许调查，报告如实记录**——按此实现并在测试与 issue 注记写明
- 调查中途异常（如 harness 抛非预期异常）：API 层兜底不泄漏堆栈，归 500 + 结构化错误体

TDD 顺序建议：POST 契约（404 / 200 键集合 / mock 脚本端到端）→ 开局锚点 → GET 契约（200 / 404 / escalated 可查 / 覆盖语义）→ status ≠ investigating 语义 → 缺省依赖 503。

## 3. 边界（勿越）

- 只改 `src/oncall/api/routes.py` + `src/oncall/ingest/app.py`（组装点）+ `src/oncall/api/` 下新建文件（如 `investigation.py` / 报告注册表）；**不改 harness 包内任何文件、不改 db/models.py、不改 pyproject、不改 M2 既有文件**——发现必须改的，停手在 issue 注记记录
- 零 LLM 真实调用、零 SDK import；API 测试全部 `inproc_asgi`，harness 组件全部 mock 注入
- 不建任何 ORM 表（D-25：报告注册表进程内存持有，M4 替换）；holdout（`datasets/golden/holdout/`）禁读
- **harness 包仍禁 import api**（C3 方向不可逆：只允许 api → harness 单向依赖）；不做 M7 runner、不做 M8 UI、不做真实 Planner client（T8）
- ruff：C6 单文件 ≤300 行（routes.py 现 125 行有余量，若膨胀优先拆新文件）；E501 ≤100；踩坑②多参收拢 dataclass；pydantic 入参契约 extra=forbid
- 提交规范：中文 + type 前缀，预期 `feat(M3-调查循环): ...`；body 写**为什么**；引用 `.scratch/m3-investigation-loop/issues/07-investigate-api.md`；不跳过 hooks

## 4. 已知踩坑（M0–M3 实录，沿用 M3-06 增补版）

- ① 同文件多次编辑必须**串行**，下一回合 grep 复核落盘（M3-06 再次实锤：4 并发 Edit 丢 3）
- ② ruff：PLR0913 max-args=5——`create_app`/`create_router` 新增注入参数多时收拢 dataclass（照 T6 `LoopComponents` 先例）
- ③ A2：>200 字符模板文本落模块级常量
- ④ pydantic frozen 模型不可变——序列化用 `model_dump(mode="json")`，datetime/StrEnum 注意 JSON 化
- ⑤ pytest addopts 已含 `-q`——计数用单层 `-q` 输出（450→只增不减）
- ⑥ inproc_asgi 标记勿漏（否则 A1 断网 fixture 会掐 TestClient 的 socket）
- ⑦ E501 ≤100，中文 docstring 易超，写完先扫
- ⑧ 交前 `ruff format` 过一遍；注意 format 会强制**顶层 def 两空行**且展开 `__all__` 魔法逗号（M3-06 因 C6 被迫移除 __all__——新文件若放报告注册表，行数预算先算）
- ⑨ 异常族语义对齐 harness（PlannerError 族在 harness 内自持）——API 层捕获后转 503/500，勿 import classify
- ⑩ mock Planner 剧本耗尽稳定回落默认收束——契约测试断言前确认剧本长度覆盖被测路径
- ⑪ `session.conclude()/escalate()/abort()` 单次迁移守卫——重跑同 incident 调查须**新建 session**，勿复用已终态对象
- ⑫ C3 守卫会抓 harness 内 import api——api 目录 import harness 方向合法，勿反向加依赖

## 5. 验证路径（收尾清单）

- [ ] issue 07 验收四条逐项打勾（POST 契约 / GET 契约 / 开局锚点 / 全量门禁），在 issue 文件内回填注记（含任何边界偏差与理由）
- [ ] 门禁：pytest（基线 450 passed / 4 skipped **只增不减**）+ ruff check + ruff format --check 全绿
- [ ] 架构守卫全绿：C6 / C8 / A1 / A2 / C3（api → harness 单向）
- [ ] issue 文件 `Status:` 翻 `resolved`；spec.md 状态节 M3-C 勾选 07
- [ ] 收尾汇报：落位文件清单 + 测试计数 + POST/GET 响应键集合对照表 + 下一票（08 端到端 3 剧本验证，真实 LLM 调用前需用户确认 key）就绪确认
