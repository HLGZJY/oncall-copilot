# M4-05 派工 prompt：缺口① opening 视图（T5 / G8；D-37）

oncall-copilot M4 第 5 票：修复 M3 issue 08 注记①——Planner 决策视图缺事件
锚点（模型开局面盲，实测结论直呼『未提供告警关联的服务名称及时间窗口』）。
按 **D-37** 定案：`_decide_with_retry` 的 view 增 **`opening` 键** = D-17 卡片
精简投影；视图组装整体抽 `context_manager.build_decision_view`，loop.py 瘦身
保 C6。**严格 TDD；零 LLM 真实调用、零 HTTP 外呼；只补「服务名 + 时间窗 +
源可用性」这类开局锚点，投影而非全卡片（context.items 全文不进 view）。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`d8fdd5e`**（M4-04 report.md 已落位），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests --tb=no -p no:warnings -q`（当前 **523 passed / 7 skipped，coverage 98%**）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest 输出统计行用重定向 + 退出码取（管道 grep 会吞退出码，且 `-q` 时统计行在 stderr——用 `2>&1 | tail -1` 或非 quiet 模式取）
- 容器栈不需要起；不需要任何 LLM API key；测试一律 mock harness 组件（照 T6 先例）+ 内存 SQLite（勿写坏仓库根的现库 `oncall.db` 文件）
- **与 06 的关系：本票先做**。06 与本票同碰 `context_manager.py` 且其预算断言写明「含 opening 后的完整 prompt」——06 必须以本票落位后的基线开工，两会话勿并行。

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票复用：事件卡片 / D-17、调查会话 / Investigation Session；**无新术语**，若确需造词当场入表并写 `_Avoid_`）
3. `.scratch/m4-evidence-chain/issues/05-opening-view-gap1.md`（**权威票面**，下方为摘要）+ `.scratch/m4-evidence-chain/spec.md`「关键契约」节
4. `docs/design/m4-evidence-chain-design.md`——重点：§技术方案 T5 行、开放点 G8 定案（= D-37）、§开发计划 T5 行
5. `docs/design/decisions.md`：**D-37**（view 增 `opening` 键 = D-17 卡片精简投影 `{alertname, instance, job, severity, source, status, fired_at, last_fired_at}` + 三源 `context.status` 摘要，不含 items 全文；组装抽 `build_decision_view`）；冻结面 **D-17**（卡片键集）、**D-23**（六工具冻结不扩——本票不碰工具面）
6. M3 issue 08 注记①原文（已逐字引在票面 blockquote，理解动机用）
7. 代码（行号以基线 `d8fdd5e` 为准）：
   - `src/oncall/harness/loop.py`（**现 299 行，贴 C6 ≤300**）——`_decide_with_retry`（138 行起，view 四键字面量组装在 142–147）
   - `src/oncall/harness/context_manager.py`（现 157 行）——`build_system_prompt` / `SUMMARY_TEMPLATE` 模块级常量先例、`__all__` 导出面
   - `src/oncall/harness/planner.py`（119 行）——接缝 docstring 对视图的承诺（『记忆摘要 + 事件锚点』），本票兑现处
   - `src/oncall/api/investigation.py`——`opening_builder` 依赖注入点（约 182–221 行）：D-17 卡片在 API 组装，**经依赖注入传入 loop，不反向拉 API**
   - `tests/unit/test_harness_loop.py`（T6 mock 先例）+ `tests/unit/test_investigation_api.py`（opening 组装与留存先例）
8. `docs/conventions/`（工程纪律权威）

## 2. 任务（权威票面 = issue 05，下方为摘要）

落位：`harness/context_manager.py`（新函数 `build_decision_view`）+ `harness/loop.py`（瘦身调用）+ opening 注入链 + 测试新增。

- **`build_decision_view(session, notices, opening)`**：view 四键（system_prompt / steps / hypotheses / notices）组装从 `_decide_with_retry` 原样搬入并增第 5 键 `opening`；`opening` 缺省 None 时键仍存在、值为 None（键集合稳定，MockPlanner script 回放不炸）
- **opening 键集合（D-37 定案，精确断言）**：`{alertname, instance, job, severity, source, status, fired_at, last_fired_at}` + 三源 `context` 的 `status` 摘要（ok/unavailable 标记，**不含 items 全文**——投影非全卡片断言必写）；卡片缺字段（如 instance）容缺处理，测试钉一种缺失形态
- **opening 注入链**：API `opening_builder` 产出卡片 → 传入 `run_investigation`（加关键字参数，缺省 None，向后兼容既有测试）→ `_decide_with_retry` → view；不反向拉 API、不改 `db/views.py`
- **loop.py 净减**：view 组装 5 行换 1 行调用；落位后 `wc -l`：loop.py ≤300、context_manager ≤300（现 157，预算 +40 行内）；C6 达标即拆分预案（不另拆文件）

TDD 顺序建议：`build_decision_view` 单测（键集合精确 + opening 投影 + None 容缺）→ loop 接缝（view 含 opening 键、None 缺省不破坏既有）→ 注入链 e2e（API 传卡片 → view 收到）→ 既有 mock e2e 3/3 回放零回退 → 全量门禁。

## 3. 边界（勿越）

- 生产代码只动：`harness/loop.py`、`harness/context_manager.py`、`api/investigation.py`（仅 opening 注入链那几行）；**不改** `harness/planner.py` 接缝签名、`db/views.py`、`db/models.py`、D-17 卡片键集、六工具注册面
- ruff：`max-args=5`（`build_decision_view` 三参安全）、PLR0911 出口 ≤6、函数 ≤50 语句；C6 两文件 ≤300 行落位后必查
- 零 LLM 真实调用、零 HTTP 外呼；`datasets/golden/holdout/` 禁读
- 提交规范：中文 + type 前缀，预期 `feat(M4-证据链): ...`；body 写**为什么**（投影而非全卡片的 token 预算理由 + loop.py 瘦身保 C6）；引用 `.scratch/m4-evidence-chain/issues/05-opening-view-gap1.md`；不跳过 hooks

## 4. 已知踩坑（M0–M4 实录，含本票针对性）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ② ruff：`max-args=5`、PLR0912 ≤12、PLR0913 ≤5、函数 ≤50 语句、PLR0911 ≤6
- ③ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture；API 测试用 `pytest.mark.inproc_asgi`
- ④ **loop.py 299 行贴 C6**：先抽后增，每编辑一步 wc -l 核对；context_manager 新增 ≤40 行预算
- ⑤ **view 键集合是 MockPlanner 之外的隐性契约**：`hypotheses` 键是 text 列表（非对象）、steps 是 summarize_step 摘要——搬入 `build_decision_view` 时逐字照搬不改语义，既有 3 剧本回放是回归锚
- ⑥ pytest 输出统计行：重定向 + 退出码取，统计行在 stderr（`-q` 模式）
- ⑦ opening 卡片里 datetime 字段渲染：沿 `_step_ts_iso` / pydantic `Z` 后缀口径，勿自己 `isoformat()` 拼出 `+00:00`

## 5. 验证路径（收尾清单）

- [ ] issue 05 验收五条逐项打勾（view 含 opening 键集合精确 / 不含 context.items / loop.py 与 context_manager 双 ≤300 / mock e2e 3/3 / 全量门禁），在 issue 文件内回填注记
- [ ] 架构守卫全绿：`tests/test_architecture_guards.py`（C3/C4/C5/C6/A2/C8）
- [ ] 门禁：pytest（基线 **523 passed / 7 skipped 只增不减**，coverage ≥98%）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；`.scratch/m4-evidence-chain/spec.md` 任务序列表 05 行勾选
- [ ] 收尾汇报：落位文件清单 + 行数对账（loop.py / context_manager 落位后 wc -l）+ view 形状样例（交用户复核）+ 06 就绪确认（06 以本票落位为基线）
