Status: resolved
Blocked by: 无（M3-01~08 已 resolved；M7 issue 07 提供实测证据）

# 09 planner 取证策略升级：先取证后 KB + 调查视图事件锚点（M7 issue 07 全 miss 复盘）

## 背景（实测证据，禁虚构）

M7 issue 07 批3（dev 全集 12×2×3，eval_runs id 3100–3171）**72 格全 miss、0 unstable**：
failure_mode = tool_error 54（planner 假设仅由 query_kb 支撑 → M6-T5 知识污染防线
VerifierError 拦截）+ plan_error 9（畸形输出重试耗尽）+ timeout 8 + unknown 1。
人工抽检 32 行（judge 18 全量 + rule 14 随机）✔ 32/0 误判——判定接缝可信，
全 miss 系 harness 证据面缺口，非性能/成本/评测误判问题。

根因链：planner 调查视图缺事件锚点（M5 issue 08 注记）→ planner 倾向直接 query_kb
查历史事故 → KB 证据不可独立证实假设 → M6-T5 防线按设计拦截 → 无有效假设/结论。

## 任务

- **决策策略升级**（planner system prompt / 调查引导面）：先取证后 KB——强制首轮
  消费事件锚点与取证工具输出，KB（query_kb）降级为「取证后参考召回」，禁止假设
  仅由 KB 支撑（与 M6-T5 防线对齐而非绕过）；
- **调查视图事件锚点**：planner 输入注入告警时间线/事件锚点（复用 golden evidence
  面的 timeline 形态，M5 issue 08 注记兑现），消除「只有拓扑没有事件」的盲调查态；
- **M6-T5 交互回归**：新增测试覆盖「新取证工具产出证据支撑假设 → Verifier 正常
  证实」路径，确保升级后不被误拦；
- **mock 档回归**：现有 mock planner 测试全绿（策略升级不得破坏既有接缝）。

## 模型档位（用户拍板 2026-09-10：百炼专用端点，冒烟已通）

端点/key 沿用 `C:\Users\heguo\.oncall-llm-env`（`ONCALL_LLM_*`，冒烟脚本
`C:\Users\heguo\oc-llm-smoke.py`；qwen3.7-flash JSON Mode + `enable_thinking: False`
已验证通过）。**eval profile env 切换（G8），不硬编码模型名**：

| 角色 | 模型 | 余量（tokens） | 说明 |
|---|---|---|---|
| 被评·性价比基线 | qwen3.7-flash | 859,041 / 1,000,000 | 现役默认，冒烟通过 |
| 被评·旗舰上探 | qwen3.8-max | 1,000,000 / 1,000,000 | 满额 |
| judge（防自评） | deepseek-v4-flash-0731 | 225,559 / 1,000,000 | 异厂商家族，余量低→仅 judge 小 token 面 |

备用：qwen3.7-flash-2026-07-15（510,941）；qwen3.5-ocr（1M，无 OCR 需求不动）。
真实调用只发生在 ONCALL_RUN_M7_EVAL=1 解锁的 integration 路径（D-58 惯例不变）。

## 验收（可机械判定）

- [ ] planner 策略升级后，mock 档全量 pytest 只增不减（基线 820）+ ruff 双检 0 错
- [ ] 新增「取证证据支撑假设 → Verifier 证实」回归测试通过；「仅 KB 支撑 → 拦截」防线测试仍通过
- [ ] 真实档冒烟（ONCALL_RUN_M7_EVAL=1）：qwen3.7-flash × 1 剧本 × 1 遍产出**非空结论**且 0 次 VerifierError
- [ ] 冒烟 usage 实测（tokens/延迟）回填本 Comments；dev 全量重跑留给后续验证票（预算另拍板）

## Comments

- 2026-09-10 建票：M7 issue 07 全 miss + 人工抽检 32/32 判定公道坐实 harness 缺口；
  百炼端点冒烟通过（qwen3.7-flash，JSON Mode + 关思考）。档位拍板：qwen3.7-flash +
  qwen3.8-max 被评、deepseek-v4-flash judge（余量 225K 只够 judge 小 token 面）。
- 2026-09-10 落位（T9 执行期注记）：
  - **①取证策略进系统提示**（`context_manager.FORENSICS_FIRST_STRATEGY`）：先取证后 KB、
    KB 降级参考召回、禁 KB-only 假设（与 M6-T5 对齐非绕过）+ 同参重复禁令与及时收束准则；
  - **②调查视图事件锚点**：`project_opening` 按需透传 `event_anchors`（复用
    eval/evidence.py timeline 形态，D-37 键集合契约不破坏）；eval runner `_run_once`
    由 golden 告警时间线构建 opening 注入（D-18 同源零标注泄漏）；**真实 client
    `_messages_of` 此前丢弃 opening——模型根本看不到，已修复**（根因链实锤一环）；
  - **③顺带修复两个吞结论的暗坑（探针实测定位）**：(a) `summarize_step` 指标只认
    `query` 键，query_metrics/search_logs 步骤摘要恒 n/a → planner 无法分辨已查内容
    → 同参重复烧步数，补 promql/selector 回退；(b) 输出协议原文「收束仅
    `{"conclusion"}`」与 `PlannerDecision.thought` 必填契约矛盾——模型照协议收束必炸
    plan_error，协议文案改为与契约一致（thought 恒必填，D-22 契约面零改动）；
  - **④CONTEXT.md** 新增「事件锚点 / Event Anchors」词条；
  - **门禁**：collect 833（基线 820 + 新增 13）全绿 exit=0，ruff check + format 双绿；
    新测试 `tests/unit/test_m3_issue09_forensics_first.py`（13 例）：取证证实 / 仅 KB
    拦截 / 混合支撑不拦 / 事件锚点注入与零泄漏 / 真实 client 透传 opening；
  - **真实档冒烟（ONCALL_RUN_M7_EVAL=1，qwen3.7-flash × cpu-spike × 1 遍，
    profile env 切换 G8 零硬编码）**：verdict=**top3**（judged_by=judge，
    假设方向命中 CPU 打满）· **结论非空 ✔** · failure_mode=None · **0 次 VerifierError ✔**
    · 6 步 / 15.6s · usage 实测：7 次调用，prompt 10,744 + completion 1,091 =
    **11,835 tokens**，延迟 15.564s（单价 env 未配，cost 列显式 0 可追溯）。
    对照 issue 07 同剧本：tool_error(VerifierError 拦截) → 全 miss；本票后防线不再
    误拦、KB 污染消失。冒烟共 6 次尝试（含 3 次 15 步熔断/绕圈熔断、2 次 plan_error
    ——即暗坑 a/b 的定位过程），最终一次达标；行为方差仍大，dev 全量重跑留验证票。
- 2026-09-10 resolved：验收四项全过。
