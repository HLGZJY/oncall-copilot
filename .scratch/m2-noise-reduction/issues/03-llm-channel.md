Status: ready-for-agent
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

- [ ] 输出契约单测绿：合法 JSON 解析通过；畸形 JSON 重试 ≤2 次后落 risk（channel=llm_error）
- [ ] 超时兜底单测绿：client 超时 → 落 risk，不抛出
- [ ] few-shot 泄漏守卫测试绿：样本池断言不含 3 验证剧本与任何 holdout 路径
- [ ] prompt 组装快照测试绿：卡片输入 → 稳定 prompt 结构（三态定义 / few-shot / 输出协议齐全）
- [ ] 全量 pytest + ruff 绿

## Comments

-
