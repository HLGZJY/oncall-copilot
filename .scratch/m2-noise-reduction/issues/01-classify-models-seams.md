Status: resolved
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

- [x] verdict 三态枚举与 `ClassificationResult` 序列化/反序列化单测绿（2026-09-07，8 例：三态/channel 冻结、dict 与 JSON 字符串双 roundtrip、非法枚举与 confidence 越界拒绝）
- [x] 置信度阈值派生单测绿：低于阈值 → risk、等于/高于 → 原判定；`risk_confidence_threshold` 配置生效（2026-09-07，7 例：0.69→risk、恰等 0.7→原判定（严格小于钉死）、0.0 误报判仍派生 risk、自定义阈值 0.5 覆盖默认且等值边界同样严格小于、审计字段随派生落位）
- [x] Mock client 契约单测绿：固定结论 / 畸形输出 / 超时三种夹具可用（2026-09-07，7 例：`LLMVerdict` 可编程 + 调用入参记录、`LLMOutputError`/`LLMTimeoutError` 夹具、script 按序消费耗尽后稳定回落默认（供 issue 03 重试编排）、夹具同样受 confidence ∈ [0,1] 契约约束）
- [x] 全量 pytest + ruff check + ruff format --check 绿（2026-09-07：pytest 96.31%（门禁 80%），classify 模块 4 文件均 100% 覆盖；ruff 双检通过）

## Comments

- 落位：`src/oncall/classify/`（`models.py` 领域模型 / `deriver.py` 阈值派生纯函数 / `client.py` 接缝 + Mock / `__init__.py` 公开面）
- 实现注记：
  - 枚举成员用大写（`Verdict.RISK`），值为小写串（`"risk"`）；StrEnum 保证 JSON 序列化即枚举值（G4 落库形态）
  - `derive_verdict(llm_verdict, meta, *, risk_confidence_threshold=0.7, classified_at=None)` 纯函数；`classified_at` 可注入保确定性，缺省 `datetime.now(UTC)`；成本审计经 `LLMCallMeta(model, tokens, cost_cny)` 传入（G8 口径）
  - Mock 夹具异常即契约：`LLMOutputError`（等价真实客户端 Pydantic 校验失败）/ `LLMTimeoutError`（G3 30s，mock 不真等）；script 支持 `LLMVerdict` 与异常实例混排按序回放
  - 零真实 HTTP（断网 fixture 天然满足）、零 schema 变更（建表/增列是 issue 04）、命名全用 CONTEXT.md 词汇，未造新词（verdict/confidence/channel/阈值派生均为 G3/D-19 既有定案表述，无需入表）
