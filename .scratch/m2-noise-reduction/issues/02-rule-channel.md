Status: ready-for-agent
Blocked by: 01

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

- [ ] 每条规则正反例单测绿：命中 → false_positive + reason；不命中 → pass
- [ ] 注册表编排单测绿：首条命中即短路直判；全部 pass → 交 LLM 通道
- [ ] 规则命中行 0 次 LLM 调用（编排层计数断言）
- [ ] 全量 pytest + ruff 绿

## Comments

-
