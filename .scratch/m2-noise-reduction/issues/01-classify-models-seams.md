Status: ready-for-agent
Blocked by:

# 01 分类领域模型与接缝（T1 / G3 定案）

## 任务

M2 分类的领域模型与可替换接缝，落 `src/oncall/classify/`：

- **verdict 三态**：`false_positive / risk / incident` 枚举 + `ClassificationResult` 结构（verdict/confidence/reason/channel/model/tokens/cost_cny/classified_at，channel ∈ `rule / llm / llm_error`）
- **风险派生**：LLM 输出 `{verdict: false_positive|incident, confidence: float∈[0,1], reason}`，`confidence < risk_confidence_threshold`（默认 0.7，配置可覆盖）时派生 risk（D-07）
- **LLM client 接缝**：接口（`classify(alert_card) -> LLMVerdict`）+ `MockLLMClassifier` 实现（设计期唯一实现，返回可编程的固定结论）；真实 client 留 issue 03/07 接

## 要点

- 阈值派生是纯函数，TDD 红绿先行；边界（confidence 恰等于阈值）行为要在测试里钉死
- Mock client 的返回要能覆盖：正常三态来源、畸形输出、超时——为 issue 03 的兜底测试提供夹具
- 术语纪律：一律用 CONTEXT.md 词汇（逻辑告警/规则通道/LLM 通道），不造新词

## 验收（可机械判定，实测后回填）

- [ ] verdict 三态枚举与 `ClassificationResult` 序列化/反序列化单测绿
- [ ] 置信度阈值派生单测绿：低于阈值 → risk、等于/高于 → 原判定；`risk_confidence_threshold` 配置生效
- [ ] Mock client 契约单测绿：固定结论 / 畸形输出 / 超时三种夹具可用
- [ ] 全量 pytest + ruff check + ruff format --check 绿

## Comments

-
