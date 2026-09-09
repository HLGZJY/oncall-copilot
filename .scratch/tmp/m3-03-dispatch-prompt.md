# M3-03 派工 prompt：取证工具实现（T3）

oncall-copilot M3「自主根因调查循环」第 3 票：四个真实取证工具——query_metrics（Prometheus query_range）/ search_logs（Loki query_range）/ detect_anomaly（纯统计 v1）/ get_topology（复用 oncall.context 三源），落 `src/oncall/harness/tools/`（分文件，C6 ≤300 行），并接入 M3-02 已落的 ToolRegistry 注册面。**严格 TDD，本票零 LLM 真实调用；HTTP 一律经 `oncall.infra.http.Fetcher` 注入（C4 收口），测试用 Fetcher 替身、断网单测全绿。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`a8877f3`**（M3-02 已落 ToolRegistry + PermissionGate），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests -q --cov=src/oncall`（当前 **343 passed / 4 skipped，coverage 97.35%**）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest 输出用 `> file; PYEXIT=$?` 取退出码（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key；Prometheus/Loki 用 Fetcher 替身 mock，零真实网络
- 上一票（M3-02）产物可直接复用：`src/oncall/harness/tools/`（`schemas.py` 六工具入参 schema 已冻结 / `registry.py` 的 `ToolHandler` Protocol、`ToolTimeoutError` / `ToolTransportError` 重试语义、`ToolRegistry.execute` 四态封装）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：工具 / Tool、工具结果 / Tool Result（D-23 四态）、事件 / Incident；**新术语当场入表并写 `_Avoid_`**）
3. `docs/design/m3-investigation-loop-design.md`（**权威设计，status: reviewed**）——重点：§技术方案 G2（①②⑤四态形状与 series/limit 上限）+ G3（纯统计 v1，IsolationForest 不引入）+ §开发计划 T3 行 + §风险清单 R3/R4/R5
4. `docs/design/decisions.md`：**D-23（六工具统一返回形状——本票核心依据）**、D-16（上下文三源统一形状 `{source, status: ok|unavailable, items, meta}`、上下文缺失 ≠ 调查失败、永不抛错）、D-24（detect_anomaly 纯统计 v1 选型）、D-08（RAG 是工具不是架构）
5. `docs/architecture/architecture.md` §3.2（ToolRegistry 只做注册/校验/执行，**不改写语义**——工具实现侧不得截改 Registry 职责）+ §3.4 防失控三闸（工具层异常映射为四态，不向上抛原始异常）
6. **代码先例（照抄结构，不抄业务）**：
   - `src/oncall/context/promql.py`（`PromClient` 解包与降级先例——get_topology 复用 `service_topology` / `collect_context`）
   - `src/oncall/context/config.py`（`ContextConfig` 窗口/URL/超时口径——时间锚缺省 ± D-16 窗口照此）
   - `src/oncall/harness/tools/registry.py`（M3-02：`ToolHandler` Protocol——四工具执行函数签名 `__call__(args, *, timeout_seconds)` 必须对齐；`ToolTimeoutError`/`ToolTransportError` 抛出语义——超时与传输错误才可重试）
   - `src/oncall/harness/tools/schemas.py`（入参 schema 已冻结，不得改字段名）
   - `src/oncall/infra/http.py`（`Fetcher` Protocol——本票唯一的 HTTP 接缝）
7. 测试基建：`tests/unit/`（裸 import conftest；autouse 断网 fixture 天然满足本票）、`tests/test_architecture_guards.py`（C6 单文件 ≤300 行 / C4 裸 HTTP / C8）

## 2. 任务（权威票面 = `.scratch/m3-investigation-loop/issues/03-forensic-tools.md`，下方为摘要）

### `src/oncall/harness/tools/metrics.py` query_metrics

- 入参 `{promql, start?, end?, step?}`（schema 已冻结）：时间缺省锚 **`incident.alert.last_fired_at` ± D-16 窗口**（窗口口径照 `ContextConfig`）
- 经 `Fetcher` 调 Prometheus `/api/v1/query_range`（R3）；matrix 结果 **series 上限 20 截断**，每 series 点列纳入 ≤2000 tokens 预算（截断语义与 M3-02 `_summarize` 对齐，勿在工具内自造第二套）
- 四态：ok（有 series）/ empty（结果为空）/ error（非 2xx、坏 JSON、PromQL 语义错——最小校验只做括号/花括号配平）/ unavailable（传输层失败）

### `src/oncall/harness/tools/logs.py` search_logs

- 入参 `{selector(LogQL), start?, end?, limit≤100, direction?}`（schema 已冻结）：limit 默认 100、direction 默认 backward（R4）
- 经同一 `Fetcher` 接缝调 Loki `/loki/api/v1/query_range`，**不新开 HTTP 通道**；四态语义同上

### `src/oncall/harness/tools/anomaly.py` detect_anomaly

- 入参 `{values[], timestamps[]}`（schema 已冻结，等长校验已在 schema 层）；**纯统计 v1**：stdlib `statistics` 实现——基线窗口 z-score + 分位数/IQR + 环比突变，零新依赖
- 返回 `{status, anomalies[], meta.method}`；IsolationForest/sklearn **不引入**（M7 前按需评审，D-24）
- 纯函数无需 Fetcher：ok（检出异常点）/ empty（无异常）/ error（序列过短等无法计算）；unavailable 不适用但保持四态接口完整

### `src/oncall/harness/tools/topology.py` get_topology

- 复用 `oncall.context` 的 `service_topology` / `collect_context`（**不在 C3 禁列**，三源实现零重建），返回 D-16 统一形状
- 依赖注入照 context 先例（`PromClient` 经 Fetcher 组装），测试注入替身

### 注册面收口

- 四工具执行函数以 `ToolHandler` 签名实现，经 `register_six_tools(registry, handlers)` 注入（M3-02 全或无契约：四 handler 齐备才注册成功）；两 stub（query_kb / execute_action）不涉及
- 工具层永不向上抛原始异常（D-16 纪律）：一切内部失败映射为 `ToolResult{status: error|unavailable, meta.reason}`

TDD 顺序建议：query_metrics 四态 → series 截断与时间锚 → search_logs limit/direction → detect_anomaly 四类统计夹具 → get_topology D-16 形状 → register_six_tools 全六注册收口。

## 3. 边界（勿越）

- 只新建 `src/oncall/harness/tools/` 下 `metrics.py` / `logs.py` / `anomaly.py` / `topology.py`（如需共享降级 helper 可加 `sources.py`，勿超）；及 `tests/unit/test_harness_tools_forensic.py`（或按工具拆多个测试文件，命名照 `test_harness_tool_registry.py` 风格）；**不改 `schemas.py` 冻结字段、不改 `registry.py`/`permission.py`、不改 pyproject、不改 oncall/context 既有文件**
- 零 LLM 真实调用、零 SDK import（A1 断网单测天然满足）；HTTP 只经 `infra.http.Fetcher` 注入（C4——工具文件内禁 import httpx）；不建任何 ORM 表（D-25）
- holdout/（`datasets/golden/holdout/`）禁读
- C6 单文件 ≤300 行（四工具已分文件，勿硬塞）；C8 模块级可变全局禁用；命名一律 CONTEXT.md 词汇（工具/工具结果），不造新词
- 提交规范：中文 + type 前缀，预期 `feat(M3-调查循环): ...`；body 写**为什么**；引用 `.scratch/m3-investigation-loop/issues/03-forensic-tools.md`；不跳过 hooks

## 4. 已知踩坑（M0–M3 实录，M3-02 新增 ⑨）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ② ruff：`max-args=5`、PLR0912 分支 ≤12、PLR0913 参数 ≤5、函数 ≤50 语句——多分支四态判定收拢成小函数
- ③ A2：本票无 prompt，勿在代码内联任何 >200 字符提示文本
- ④ pydantic `mode="json"` UTC 序列化是 `Z` 后缀；DTZ 禁裸 `datetime.now`（必须带时区）
- ⑤ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture
- ⑥ StrEnum 成员大写、值为小写串（`ToolStatus` 四值照此，勿新造）
- ⑦ E501 行宽 ≤100，中文 docstring 易超，写完先扫一遍
- ⑧ 写完直接 `ruff format` 过一遍再交，别手拧；`ruff check --fix` 可吃掉 import 排序
- ⑨ C6 守卫会抓 >300 行文件——多行 ToolSpec/构造调用改用元组表驱动压缩（照 M3-02 `_TOOL_TABLE` 先例）
- ⑩ Fetcher 替身返回 `HttpResponse(status_code=..., body=...)`，坏 JSON 直接给 body 塞非法字符串即可触发 error 分支

## 5. 验证路径（收尾清单）

- [ ] issue 03 验收六条逐项打勾（每工具四态 / 时间锚缺省 + series ≤20 / limit 与 direction 默认值 / 统计四夹具 / D-16 形状 / 全量门禁绿），在 issue 文件内回填注记
- [ ] `register_six_tools` 四 handler 注入后六工具名集合精确匹配，注册面收口单测绿
- [ ] 架构守卫全绿：C3 / C4（裸 HTTP）/ C6 / C8 / A1/A2
- [ ] 门禁：pytest（基线 343 passed / 4 skipped **只增不减**）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；spec.md 状态节 M3-A 勾选 03（M3-A 三票收官）
- [ ] 收尾汇报：落位文件清单 + 测试计数 + 四态语义与 D-16/D-23 对齐说明 + 下一票（04 ContextManager）就绪确认
