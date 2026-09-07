Status: resolved
Blocked by: 01

# 03 LLM 通道（T3 / G3/G8 定案）

## 任务

LLM few-shot 分类层，落 `src/oncall/classify/llm/`：

- **prompt 组装**：系统角色（SRE 分诊员）+ 三态定义（含 D-07 风险语义）+ few-shot 样本 + 事件卡片 JSON（D-17 13 键形状，上下文时间锚 `last_fired_at`）+ 输出协议
- **few-shot 样本池**：只取 `datasets/golden/dev/`，且**排除 3 验证剧本**（slow-sql / protocol-mismatch / false-positive-flap）；每态 K=2–3 条
- **输出契约**：JSON mode（`response_format`，R2）+ Pydantic 校验 `{verdict, confidence, reason}`；LLM 不直出 risk（阈值派生在 01 的接缝上）
- **兜底**：畸形输出重试 ≤2 次 → 仍失败落 risk（channel=llm_error）；超时 30s；成本字段按 mock 单价记录（估算法口径）
- 双模型接缝预留：DeepSeek-chat / Qwen-plus 经同一接口，本票只接 Mock，真实 client 调通在 issue 07

## 要点

- **holdout 禁看**：few-shot loader 只读 `dev/` 目录；泄漏守卫测试断言 3 验证剧本与 holdout 全部不在样本池
- prompt 总量 ≤4k tokens（系统 + few-shot + 卡片）——组装层要有 token 预算意识（截断策略记录在代码注释即可，不做精细计量）
- 设计期零真实 API 调用（用户 2026-09-07 拍板）：一切走 Mock，真实调用与成本实测是 issue 07 且需用户确认 key

## 验收（可机械判定，实测后回填）

- [x] 输出契约单测绿：合法 JSON 解析通过；畸形 JSON 重试 ≤2 次后落 risk（channel=llm_error）
  ——`test_classify_llm_channel.py`：合法路径 `test_happy_path_incident`；畸形重试耗尽 `test_malformed_output_exhausted_falls_to_risk_with_pinned_fallbacks`（3 次尝试 → risk/llm_error，confidence=0.0、reason/tokens/cost 兜底值钉死）
- [x] 超时兜底单测绿：client 超时 → 落 risk，不抛出
  ——`test_timeout_falls_to_risk_without_retry`（超时**不重试**：3×30s 会击穿延迟/成本预算；`DEFAULT_LLM_TIMEOUT_SECONDS == 30.0` 有专测钉死）
- [x] few-shot 泄漏守卫测试绿：样本池断言不含 3 验证剧本与任何 holdout 路径
  ——`test_classify_llm_fewshot.py` 四重守卫：①真实 dev 集排除 3 验证剧本；②`false-positive-flap`（06 落地后）仍被排除的回归；③loader 拒绝非 dev 目录（入口 fail-fast）；④禁复用会读 holdout 的黄金树加载器（源码级断言）
- [x] prompt 组装快照测试绿：卡片输入 → 稳定 prompt 结构（三态定义 / few-shot / 输出协议齐全）
  ——`test_classify_llm_prompt.py`：SRE 分诊员角色 + 三态定义（含 D-07「不丢弃」）+ 输出协议（LLM 不直出 risk）+ 卡片含 `last_fired_at` 时间锚 + 同输入逐字确定性
- [x] 全量 pytest + ruff 绿
  ——实测：201 passed / 4 skipped（既有 live-integration skip）；coverage 96.69%（≥80% 门禁）；`ruff check` / `ruff format --check` 全过

## Comments

- 2026-09-07（T3 执行）：落 `src/oncall/classify/llm/`（`prompt.py` / `fewshot.py` / `channel.py`）。要点：①编排层只消费 01 定案公开面（`LLMClassifier` 接缝 + `derive_verdict`），`classify/__init__.py` 导出仅追加；②成本 G8 估算法：`estimate_tokens` 2 字符≈1 token 粗估 + `MOCK_MODEL_PRICING_CNY_PER_1K` 单价快照（deepseek-chat 0.002/0.003、qwen-plus 0.0008/0.002 CNY/千tokens，取价 2026-09-07），未登记单价构造期即 ValueError（禁静默 0 成本）；③每态 K=2（G3 允许 2–3，取下限控 4k token 预算）；④调优参数收敛为 `LLMChannelOptions`（阈值/超时/时间戳）；⑤新术语已入 CONTEXT.md：`few-shot 样本池 / Few-Shot Pool`、`验证剧本 / Validation Scenario`。M2-A（T1–T3）全部完成，04（落库/API）解除全部阻塞。
