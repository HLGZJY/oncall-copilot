Status: ready-for-agent
Blocked by: 01

# closed loop report

## 任务

闭环报告拼装器 kb/report.py（读库确定性拼装：开局卡片摘要/时间线（alert_events 实测 firing 序列，逐条可回溯行 id）/根因（confirmed hypotheses + supporting steps）/处置（remediation_proposals 全链）/改进建议（LLM 门控占位文案，D-50））；GET /investigations/{id}/report.md 升级五节版（D-36 基线不删证据链节）；source_meta_json 锚点逐点回溯；报告 JSON 增可选 kb_reference 键（D-35 契约测试同步修订）

## 验收（可机械判定）

- [ ] pytest 绿：五节齐全且时间线/根因/处置各数据点可回溯库行；LLM 未启用时建议节为占位文案；既有 report.md 键/节兼容；682 基线只增不减
