Status: resolved
Blocked by: 01（已解除）

# 02 规则通道（T2 / G2 定案）

## 任务

规则谓词注册表，落 `src/oncall/classify/rules/`：

- 每条规则 = `{name, predicate(alert_event, context) -> RuleVerdict}`；RuleVerdict ∈ {`false_positive`（带 reason）, `pass`（交 LLM）}——**规则只判误报直判或放行，不判真实**（防规则误杀真事件，G2 定案）
- 初始规则集（3 条）：① resolved-only 幽灵通知（无对应 firing 的行，M1 issue 03 实录 4 条同类数据）；② 维护窗口/静默期内告警；③ 重放/迟到期已失效告警（`endsAt` 早于当前且已 resolved）

## 要点

- 规则化标准：**能写成确定性谓词的才进规则通道**；语义模糊（如"这个告警看起来不重要"）一律 pass 给 LLM
- 规则命中行 0 次 LLM 调用——规则先行是成本杠杆（成本四杠杆第 2 位）
- 不建独立抑制层/聚合层（G1）：「下游挂上游抑制」类模式如需要，将来以谓词形式进注册表，不建引擎
- 规则名是稳定契约（统计报告按规则名归因），命名走 CONTEXT.md 词汇

## 验收（可机械判定，实测后回填）

- [x] 每条规则正反例单测绿：命中 → false_positive + reason；不命中 → pass（2026-09-07，10 例：幽灵规则命中/正常 firing 放行/raw_alert 缺失放行；维护窗口命中/labels 选择子匹配与不匹配/闭区间端点命中窗外放行/无窗口放行；失效规则命中/未恢复放行/resolved_at 恰等于 now 放行——严格早于钉死）
- [x] 注册表编排单测绿：首条命中即短路直判；全部 pass → 交 LLM 通道（2026-09-07，2 例：spy 谓词断言短路后位规则不执行、幽灵行同时满足规则③时归因仍归规则①；全 pass → None 即「交 LLM」编排契约；默认规则序钉死 resolved_only_ghost → maintenance_window → stale_replay）
- [x] 规则命中行 0 次 LLM 调用（编排层计数断言）（2026-09-07：三类命中行直判返回 channel=rule，MockLLMClassifier.calls 断言为空——规则通道接口不触达 LLMClassifier，调用方只在 None 时进 LLM）
- [x] 全量 pytest + ruff 绿（2026-09-07 实测：pytest 112 passed，coverage 96.55%（门禁 80%），rules/registry.py 99%；ruff check + format --check 双检通过）

## Comments

- 落位：`src/oncall/classify/rules/`（`registry.py` 机制 + 初始规则集单文件 / `__init__.py` 公开面）；`src/oncall/classify/__init__.py` 仅追加导出，既有公开面未动
- 实现注记：
  - `RuleVerdict` = frozen dataclass 二值结构（`false_positive(reason)` / `pass_to_llm()` 类方法构造）；`Rule = {name, predicate(alert_event, context)}`，谓词吃 `AlertEvent` ORM 行（M2 消费 `status=deduped` 行，与 issue 04 落库面同源）
  - `RuleContext = {now, maintenance_windows}`——`now` 可注入保确定性；`MaintenanceWindow = {starts_at, ends_at, labels}`，labels 选择子空 dict 全局生效，判定锚点是 `last_fired_at`（静默的是「窗口内触发的告警」），窗口为闭区间（端点命中已钉死）
  - 幽灵通知判定信号：`annotations_json["raw_alert"]["status"] == "resolved"`——M1 合并逻辑把无锚点 resolved 兜底计为新 firing，落库来源通知本身是 resolved 即「无对应 firing 的行」（M1 issue 03 实录 4 条同类）；raw_alert 缺失放行防误杀
  - 失效规则边界：`resolved_at < now` 严格早于才命中；未恢复（resolved_at=None）放行——活跃告警绝不入此规则
  - **model 字段取 "rule"（非规则名）**：model 语义是「产出结论的判定器」，规则通道无模型；规则名（稳定契约）经 `reason` 前缀 `[name] ` 结构化落位供统计归因，model 维度聚合时规则行归为一类不碎化（成本统计只关心 LLM 行）。confidence=1.0 / tokens=0 / cost_cny=0 / classified_at=context.now，均已测试钉死
  - 规则名已入 CONTEXT.md「规则名 / Rule Name」词条（resolved_only_ghost / maintenance_window / stale_replay）
  - 零真实 LLM 调用、零容器操作、零 schema 变更（建表/增列是 issue 04）；命名全用 CONTEXT.md 词汇
