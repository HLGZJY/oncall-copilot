# m2-noise-reduction · Spec

M2 降噪分类。**权威设计**：`docs/design/m2-denoise-classify-design.md`（status: reviewed，2026-09-07 评审通过，G1–G9 全部定案，标准来源见该文档 §7「评审依据」R1–R8）。

## 目标

消费 `alert_events.status=deduped` 的告警 → 规则通道（谓词注册表，只判误报/放行）+ LLM 通道（few-shot 兜底，置信度低于阈值落风险，D-07）双通道三态分类（误报→归档 / 风险→观察 / 真实→建 incidents 档）；统计层按逻辑告警归并后核算降噪率与漏报（D-20 口径），3 剧本端到端验证含 1 个「历史上误报的告警」被识别为误报（PRD §7-M2）。

## 关键契约（评审已定，执行时不得擅改）

- **输入契约只消费不推翻**：D-07（不确定落风险）/ D-13（增列流程）/ D-14（指纹含时间窗桶，M1 落库不动）/ D-15（计数口径）/ D-16（三源形状）/ D-17（卡片 13 键）/ D-18（golden 同源纪律）
- **落库形态（D-19）**：`alert_events` 增列 `classification_json`（审计全量），status 枚举不扩（deduped→classified）；risk 由置信度阈值（默认 0.7 可配 `risk_confidence_threshold`）派生，LLM 只输出 `{verdict: false_positive|incident, confidence, reason}`；真实事件 1:1 建 incidents 最小集；分类走独立 `POST /classify`，**不串联 `/ingest`**
- **规则通道（G2）**：Python 谓词注册表，规则只判误报直判或放行，**不判真实**；语义模糊一律交 LLM；初始规则 = resolved-only 幽灵 / 维护窗口 / 重放失效
- **LLM 通道（G3/G8）**：DeepSeek-chat + Qwen-plus 双模型 OpenAI-compatible 接缝，Mock 先行；few-shot **只取 dev/ 且排除 3 验证剧本**；prompt ≤4k tokens；超时 30s / 重试 ≤2 / 失败落 risk（channel=llm_error）；单次调用成本上限 ¥0.05（估算口径）
- **统计口径（D-20）**：单位 = 逻辑告警（统计层归并，M1 落库不动）；降噪率 = (R − I) / R，R = Σ `dedup_count`；漏报 = golden 标注 incident 被判 false_positive，必须 = 0
- **数据纪律**：few-shot / 调参 / 验证**只碰 `datasets/golden/dev/`**，`holdout/` 一律禁看（含 raw 切片）；误报剧本与 golden `classification` 可选字段扩展走 issue 06
- **零写操作**：M2 无任何处置动作（M5 四道闸门的事）；不做抑制/聚合独立层（G1）；不做 LLM-as-judge（M7）；设计期零真实 LLM 调用（mock/估算）

## 任务序列

| Issue | 任务 | 对应设计文档 | Blocked by |
|---|---|---|---|
| 01 | 分类领域模型与接缝（verdict/阈值派生/Mock client） | T1 | — |
| 02 | 规则通道（谓词注册表 + 初始规则集） | T2 | 01 |
| 03 | LLM 通道（prompt/few-shot/契约校验/mock 成本） | T3 | 01 |
| 04 | 落库与 incidents 建档 + `/classify` 等 API | T4 | 02, 03 |
| 05 | 统计口径（逻辑告警归并 + 降噪率/漏报核算） | T5 | 04 |
| 06 | 误报剧本 `false-positive-flap` + golden 扩展 | T6 | —（与 01–05 并行） |
| 07 | 端到端 3 剧本验证 + 成本实测回填收尾 | T7 | 04, 05, 06 |

## 状态

- [x] 设计评审通过（2026-09-07，G1–G9 定案，依据 R1–R8；D-19/D-20/D-21 已登记）
- [ ] M2-A 分类内核：01–03
- [ ] M2-B 落库与统计：04–05
- [ ] M2-C 数据底座与验收：06（ready-for-human）→ 07（真实 LLM 调用前需用户确认 key）
