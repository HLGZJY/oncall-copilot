Status: ready-for-agent
Blocked by: 03

# 04 Markdown 最小版导出（T4 / G7；D-36）

## 任务

- 新增端点 `GET /investigations/{incident_id}/report.md`：返回 `text/markdown`；内容 = 证据链数据直出（步表/假设表/结论/termination/failure_mode/成本汇总），**str 模板拼接零新依赖**（D-36：不引入 jinja2/markdown 库）
- 模板落位照 A2 先例（模块级模板常量，禁业务代码内联 >200 字符）
- 时间线美化、处置记录、改进建议**不做**（M6 报告生成范围）——本票 Markdown 是 M6 的输入而非替代

## 要点

- 数据源同 03（读库），不重复实现查询逻辑
- 步渲染最小格式：`## Step N — {tool}` + thought / input_json / output_summary / ts / tokens；假设渲染含 status 与 supporting/against 步号
- `output_json` 原始全文是否入 Markdown：**不进正文**（体积不可控），正文用 `output_summary`，`output_json` 可回溯走 JSON 报告——设计文档 G7 理由的延伸，票面写明

## 验收（可机械判定）

- [ ] pytest 绿：200 + `Content-Type: text/markdown`
- [ ] pytest 绿：内容含全部步（步数 = 库内行数）、全部假设、结论、终态、failure_mode
- [ ] pytest 绿：无记录 404；escalated 会话导出可用
- [ ] pytest 绿：模板常量落位符合 A2 断言（架构守卫全绿）
- [ ] 全量门禁不回退 + ruff 双检
