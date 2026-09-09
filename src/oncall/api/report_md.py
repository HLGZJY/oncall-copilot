"""M4-T4 Markdown 最小版导出渲染（D-36）：自 api/investigation.py 抽出（C6 行数预算）。

模板与渲染逻辑原样搬移，键/格式零倒改（M7 评测样本源兼容）；M6 闭环五节
由 knowledge.report.render_closed_loop_markdown 在端点处追加（api → knowledge）。
"""

from __future__ import annotations

import json
from typing import Any

REPORT_MD_HEADER_TEMPLATE = """\
# 调查报告 incident_id={incident_id}

- termination: {termination}
- conclusion: {conclusion}
- failure_mode: {failure_mode}
- step_count: {step_count}
- total_tokens: {total_tokens}
- total_cost_cny: {total_cost_cny}
- stop_reason: {stop_reason}
- confidence: {confidence}
"""

REPORT_MD_STEP_TEMPLATE = """\
## Step {step_no} — {tool}

- thought: {thought}
- input_json: {input_json}
- output_summary: {output_summary}
- ts: {ts}
- tokens: {tokens}
"""

REPORT_MD_HYPOTHESES_HEADER = "## 假设\n"

REPORT_MD_HYPOTHESIS_TEMPLATE = "- [{status}] {text}（支持步: {supporting}｜反对步: {against}）"


def render_report_markdown(body: dict[str, Any]) -> str:
    """读库报告 dict（`investigation_report_body` 口径）→ Markdown 最小版。

    数据直出零加工：步渲染 `## Step N — {tool}` + thought / input_json /
    output_summary / ts / tokens（票面钉死格式）；`output_json` 不进正文
    （D-36/G7 延伸）；假设渲染 status 与 supporting/against 步号。
    """
    parts = [
        REPORT_MD_HEADER_TEMPLATE.format(
            incident_id=body["incident_id"],
            termination=body["termination"],
            conclusion=body["conclusion"],
            failure_mode=body["failure_mode"],
            step_count=body["step_count"],
            total_tokens=body["total_tokens"],
            total_cost_cny=body["total_cost_cny"],
            stop_reason=body["stop_reason"],
            confidence=body["confidence"],
        )
    ]
    for step in body["steps"]:
        parts.append(
            REPORT_MD_STEP_TEMPLATE.format(
                step_no=step["step_no"],
                tool=step["tool"],
                thought=step["thought"],
                input_json=json.dumps(step["input_json"], ensure_ascii=False, sort_keys=True),
                output_summary=step["output_summary"],
                ts=step["ts"],
                tokens=step["tokens"],
            )
        )
    parts.append(REPORT_MD_HYPOTHESES_HEADER)
    for hyp in body["hypotheses"]:
        parts.append(
            REPORT_MD_HYPOTHESIS_TEMPLATE.format(
                status=hyp["status"],
                text=hyp["text"],
                supporting=", ".join(str(n) for n in hyp["supporting_steps"]) or "无",
                against=", ".join(str(n) for n in hyp["against_steps"]) or "无",
            )
        )
    return "\n".join(parts) + "\n"
