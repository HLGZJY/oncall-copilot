# M4-04 派工 prompt：Markdown 最小版导出（T4 / G7；D-36）

oncall-copilot M4「证据链与过程存储」第 4 票：新增端点
`GET /investigations/{incident_id}/report.md`——证据链数据直出的 Markdown
最小版（`text/markdown`），**str 模板拼接零新依赖**（D-36：不引入 jinja2 /
markdown 库）；数据源同 03（读库），不重复实现查询逻辑。
**严格 TDD；零 LLM 真实调用、零 HTTP 外呼；不碰时间线美化 / 处置记录 /
改进建议（M6 报告生成范围——本票 Markdown 是 M6 的输入而非替代）、不碰
opening 视图（归 05）、不碰 schema 摘要（归 06）。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`b909865`**（M4-03 GET 读库已落位），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests --tb=no -p no:warnings -q`（当前 **518 passed / 7 skipped，coverage 98%**）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest 输出用 `> file; PYEXIT=$?` 取退出码（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key；测试一律内存 SQLite（**勿写坏仓库根的现库 `oncall.db` 文件**）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票复用：证据仓库 / Evidence Repository、调查记录 / Investigation Record、报告出口；本票**无新术语**，若确需造词当场入表并写 `_Avoid_`）
3. `.scratch/m4-evidence-chain/issues/04-markdown-export.md`（**权威票面**，下方为摘要）+ `.scratch/m4-evidence-chain/spec.md`「关键契约」节
4. `docs/design/m4-evidence-chain-design.md`（**权威设计，status: reviewed**）——重点：§API 变更表 report.md 行（`text/markdown` + str 模板零新依赖）、§技术方案 T4 行、开放点 G7 定案（= D-36）、§开发计划 T4 行
5. `docs/design/decisions.md`：**D-36（Markdown 本票最小版：数据直出、格式导出与内容美化分离、最小版是 M6 报告生成的输入）+ D-35（JSON 形状权威与 Markdown 分工——`output_json` 原始全文可回溯走 JSON 报告，Markdown 正文用 `output_summary`）+ D-28（escalated 报告同经出口）**；冻结面 D-17、D-25、D-31
6. 代码（行号以基线 `b909865` 为准）：
   - `src/oncall/api/investigation.py`（现 191 行）——重点：`investigation_report` GET 端点（三表查询 + 404 语义，**本票复用其查询路径**）、`REPORT_KEYS` / `build_report`（内存导出口径，不动）
   - `src/oncall/db/views.py`（现 120 行）——`investigation_report_body` 序列化器（03 落位；Markdown 渲染**不落这里**——它是 db 层 JSON 契约出口，Markdown 属 api 层出口）
   - `tests/unit/test_investigation_api.py`（GET 契约测试 + roundtrip 先例）+ `tests/unit/test_db_views_report.py`（03 的序列化器单测先例）
   - `tests/test_architecture_guards.py`——A2 守卫实现（MAX_INLINE_PROMPT=200：抓的是 **LLM 调用点 kwargs 内超长字符串**，Markdown 模板不在其列；但照 A2 精神，模板用模块级常量、禁业务代码内联拼接）
   - `src/oncall/harness/context_manager.py`（SUMMARY_TEMPLATE 模块级常量先例）
7. `docs/conventions/`（工程纪律权威）

## 2. 任务（权威票面 = issue 04，下方为摘要）

落位：`src/oncall/api/investigation.py`（新端点 + 查询路径复用）+ 模板常量 + 测试新增。

- **新端点** `GET /investigations/{incident_id}/report.md`：返回 200 + `Content-Type: text/markdown`（FastAPI 用 `Response(content=..., media_type="text/markdown")`）；无调查记录 / running 行 → 404（与 03 的 GET 同语义，D-28 escalated 同样可导出）
- **查询路径复用（勿重复实现）**：把 03 的 GET 三表查询抽私有 helper（同文件内），JSON GET 与 Markdown GET 共用；抽取时注意 PLR0911 出口 ≤6、函数 ≤50 语句、`max-args=5`
- **模板落位**：str 模块级常量（大写命名，照 context_manager 的 SUMMARY_TEMPLATE 先例），禁业务代码内联拼接长模板；`api/investigation.py` 现 191 行，加端点 + 模板预计 ≈250 行 < C6 ≤300；若超预算则拆独立小模块（如 `api/report_md.py`）并在提交 body 说明
- **步渲染最小格式**（票面钉死）：`## Step N — {tool}` + thought / input_json / output_summary / ts / tokens——`output_json` 原始全文**不进正文**（体积不可控，D-36/G7 延伸：可回溯走 JSON 报告），票面写明该口径
- **假设渲染**：text / status / supporting_steps / against_steps 步号
- **头部摘要**：incident_id / termination / conclusion / failure_mode / step_count / total_tokens / total_cost_cny / stop_reason / confidence（数据全部来自 03 的读库路径，零内存态）
- 转义不做 HTML/Markdown 转义处理（数据直出、内部消费），票面与提交 body 写明即可

TDD 顺序建议：helper 抽取后既有 GET 测试全绿（行为不变）→ Markdown 端点 200 + Content-Type → 内容断言（步数 = 库内行数、全部假设、结论、终态、failure_mode）→ 无记录 404 / escalated 可导出 / running 行 404 → A2 + 架构守卫全绿 → 全量门禁。

## 3. 边界（勿越）

- 生产代码只动：`api/investigation.py`（新端点 + helper 抽取 [+ 模板常量]）；若拆 `api/report_md.py` 才允许新增文件
- **不改**：`db/views.py`、`db/models.py`、`harness/`、`build_report` 键集合、既有 JSON GET 的 URL / 响应 / 404 语义、agent-loop-design 与 decisions.md（本票无新决策）
- ruff：`max-args=5`、PLR0912 分支 ≤12、PLR0913 参数 ≤5、PLR0911 出口 ≤6、函数 ≤50 语句；C6 单文件 ≤300 行；C8 模块级可变全局禁用（模板常量用不可变 str 没问题）
- 零 LLM 真实调用、零 HTTP 外呼；`datasets/golden/holdout/` 禁读
- 提交规范：中文 + type 前缀，预期 `feat(M4-证据链): ...`；body 写**为什么**（含模板落位选择、output_json 不进正文口径）；引用 `.scratch/m4-evidence-chain/issues/04-markdown-export.md`；不跳过 hooks

## 4. 已知踩坑（M0–M4 实录，含 03 票新增）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ② ruff：`max-args=5`、PLR0912 ≤12、PLR0913 ≤5、函数 ≤50 语句、PLR0911 ≤6——helper 抽取后出口数先数一数
- ③ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture；API 测试用 `pytest.mark.inproc_asgi`
- ④ SQLite FK 默认不强制——测试 engine 需 `PRAGMA foreign_keys=ON` 事件监听（照 `test_db_evidence_models.py` 先例，直接抄）
- ⑤ StrEnum 值归一：读库取回 status 是小写串，termination 直接用
- ⑥ pytest 输出统计行用重定向 + 退出码取，别用管道 grep 吞退出码
- ⑦ **pydantic `model_dump(mode="json")` 对 UTC 渲染 `Z` 后缀**（03 票实证）：Markdown 里 ts 直接用读库行的 `_step_ts_iso` 口径（`Z` 后缀），勿自己 `isoformat()` 拼出 `+00:00` 造成 JSON/MD 双报告时间不一致
- ⑧ `investigations.opening_card_json` 列已存在（03 票裁决 B）——Markdown **不含 opening_card**（票面内容清单没有它，M8/M6 再消费）；现库 `oncall.db` 是 T1 旧形状缺该列，与内存测试无关，T8 实测前统一 ALTER
- ⑨ TestClient 断言 `text/markdown` 用 `resp.headers["content-type"].startswith("text/markdown")`（FastAPI 可能带 charset 后缀）
- ⑩ Markdown 内容断言用子串包含（`in resp.text`）而非全量比对——模板措辞允许微调，断言锚定数据本身

## 5. 验证路径（收尾清单）

- [ ] issue 04 验收五条逐项打勾（200 + text/markdown / 内容含全部步与假设结论终态 failure_mode / 404 + escalated 可导出 / A2 模板落位 / 全量门禁），在 issue 文件内回填注记
- [ ] 架构守卫全绿：`tests/test_architecture_guards.py` 全绿（C3/C4/C5/C6/A2/C8）
- [ ] 门禁：pytest（基线 **518 passed / 7 skipped 只增不减**，coverage ≥98%）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；`.scratch/m4-evidence-chain/spec.md` 任务序列表 04 行勾选
- [ ] 收尾汇报：落位文件清单 + 测试计数 + 模板落位与 output_json 口径说明 + report.md 内容样例片段（交用户复核）+ 下一票（05 opening 视图 / 06 schema 摘要均可并行，07 仍被 05/06 阻塞）就绪确认
