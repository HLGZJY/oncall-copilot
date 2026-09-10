Status: ready-for-agent
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
