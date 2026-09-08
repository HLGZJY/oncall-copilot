Status: resolved
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

- [x] pytest 绿：200 + `Content-Type: text/markdown`
- [x] pytest 绿：内容含全部步（步数 = 库内行数）、全部假设、结论、终态、failure_mode
- [x] pytest 绿：无记录 404；escalated 会话导出可用
- [x] pytest 绿：模板常量落位符合 A2 断言（架构守卫全绿）
- [x] 全量门禁不回退 + ruff 双检

## 落位注记（2026-09-08）

- 端点 + 模板常量 + `_render_report_markdown` + 共用查询 helper `_load_report_body` 均落 `src/oncall/api/investigation.py`（291 行 < C6 300，未拆 report_md.py）；JSON GET 与 Markdown GET 共用读库路径，不重复实现查询
- `output_json` 原始全文不进正文（D-35 分工：可回溯走 JSON 报告），正文用 `output_summary`；ts 沿用 `investigation_report_body` 的 `_step_ts_iso` 口径（Z 后缀，与 JSON 报告一致）
- 不做 HTML/Markdown 转义（数据直出、内部消费，票面口径）
- 门禁：523 passed / 7 skipped（基线 518 + 本票 5）、coverage 98%、ruff check / format 双绿、架构守卫全绿
