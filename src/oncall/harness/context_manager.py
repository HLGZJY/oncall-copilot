"""ContextManager：上下文预算管理（架构 §3.3 / §3.2，D-25/D-27③）。

职责边界（架构 §3.2）：只做**预算与摘要**，不生成计划、不改写工具语义。

- 摘要模板固定：组件/指标/异常方向/时间窗四要素齐全、同输入同输出
  （确定性——压缩 KV cache 重复前缀的前提）；变量缺失用确定性占位 `n/a`
- 工具输出预算：单次 ≤2000 tokens 只截「进上下文的摘要」，原始
  `output_json` 完整落 session（G4 指针语义：M3 = EvidenceStep 对象引用，
  M4 建表后替换为行 id，接口不变）；指针与 Registry `_summarize` 同语义
- 系统提示预算：≤1500 tokens——角色 + 工具一览（从 `TOOL_SPECS` 生成，
  勿手抄第二份清单）+ 输出协议（D-22）；工具详情 just-in-time 不预载
- 步数 ≥10 移出已证伪假设：**视图层过滤**（组件 frozen 不可变，踩坑④），
  `session.hypotheses` 全量保留；步数阈值与步数读取可注入（G8 判据 1）

token 估算双口径说明：Registry 管工具输出截断用「字符数 ÷4」（保守）；
本模块默认「2 字符 ≈ 1 token」（票面 G8 / M2 同款粗估口径）——估算函数
可注入替换，两口径并存不改 Registry（冻结文件，冲突先在 issue 注记记录）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from oncall.harness.session import HypothesisStatus
from oncall.harness.tools.registry import TOOL_SPECS

if TYPE_CHECKING:
    from oncall.harness.session import EvidenceStep, Hypothesis, InvestigationSession

__all__ = [
    "DEFAULT_EVICTION_THRESHOLD",
    "SUMMARY_TEMPLATE",
    "SYSTEM_PROMPT_TOKEN_LIMIT",
    "TOKEN_BUDGET",
    "build_decision_view",
    "build_system_prompt",
    "estimate_tokens",
    "summarize_step",
    "truncate_to_budget",
    "visible_hypotheses",
]

TOKEN_BUDGET = 2000  # G4：单次工具输出进上下文 ≤2000 tokens
SYSTEM_PROMPT_TOKEN_LIMIT = 1500  # 架构 §3.3：系统提示 ≤1500 tokens
DEFAULT_EVICTION_THRESHOLD = 10  # 架构 §3.3 / D-27③：步数 ≥10 移出已证伪假设

TokenEstimator = Callable[[str], int]

SUMMARY_TEMPLATE = "组件={component}｜指标={metric}｜异常方向={direction}｜时间窗={window}"

NA_PLACEHOLDER = "n/a"

SYSTEM_ROLE = (
    "你是自建服务的根因调查执行体：依据证据步与假设提出/验证猜测，"
    "每步只依据调查会话中已有的证据决策，不臆测证据之外的信息。"
)

_TOOL_TABLE_HEADER = "## 工具一览（名称：一句话说明；工具详情 just-in-time 按需查，不预载）"

OUTPUT_PROTOCOL = """\
## 输出协议（二选一互斥，只输出一个 JSON 对象）
- 继续调查（选工具）：{"thought": "<一句话依据>", "next_tool": "<工具名>", "args": {<入参>}}
- 证据已足够（收束）：{"conclusion": "<结论与依据>"}"""

_TIME_ANCHOR_NOTE = (
    "时间窗缺省口径：以告警 last_fired_at 为锚取近期窗口（查「告警发生时」的状态）。"
)


def estimate_tokens(text: str) -> int:
    """默认粗估 tokens（票面 G8 口径：2 字符 ≈ 1 token，中英混排误差可接受）。"""
    return (len(text) + 1) // 2


def _element(mapping: dict[str, object], key: str) -> str:
    """模板变量确定性提取：缺失/空值占位 n/a，不留 f-string 残片（踩坑⑩）。"""
    value = mapping.get(key)
    return NA_PLACEHOLDER if value is None or value == "" else str(value)


def summarize_step(step: EvidenceStep) -> str:
    """证据步 → 四要素摘要（纯函数，同输入同输出）。

    四要素来源（确定性约定）：组件 = `input_json["component"]`；
    指标 = `input_json["query"]`；异常方向 = `output_json["direction"]`；
    时间窗 = `input_json` 的 start/end。缺失一律 `n/a`。
    """
    start = _element(step.input_json, "start")
    end = _element(step.input_json, "end")
    window = NA_PLACEHOLDER if NA_PLACEHOLDER in (start, end) else f"[{start} ~ {end}]"
    return SUMMARY_TEMPLATE.format(
        component=_element(step.input_json, "component"),
        metric=_element(step.input_json, "query"),
        direction=_element(step.output_json, "direction"),
        window=window,
    )


def truncate_to_budget(
    summary: str,
    *,
    step_no: int,
    limit: int = TOKEN_BUDGET,
    estimator: TokenEstimator = estimate_tokens,
) -> tuple[str, int, bool]:
    """摘要 ≤ limit tokens；超限只截进上下文文本，原始输出留 session（G4）。

    返回 `(摘要, tokens, 是否截断)`；截断时追加 `[truncated, full at step N]`
    指针（与 Registry `_summarize` 语义对齐，勿造第二套口径）。
    """
    tokens = estimator(summary)
    if tokens <= limit:
        return summary, tokens, False
    pointer = f"[truncated, full at step {step_no}]"
    keep = max(0, limit * 2 - len(pointer) - 1)  # 默认口径一次到位；其他口径下方按比例收缩
    out = f"{summary[:keep]}\n{pointer}"
    while keep > 0 and estimator(out) > limit:
        keep = max(0, keep - max(1, keep // 2))
        out = f"{summary[:keep]}\n{pointer}"
    return out, estimator(out), True


def build_system_prompt(estimator: TokenEstimator = estimate_tokens) -> str:
    """组装系统提示：角色 + 六工具一览（TOOL_SPECS 生成）+ 输出协议（D-22）。

    组装后整体断言 ≤1500 tokens（架构 §3.3）；超限属模板膨胀事故，直接抛错。
    """
    lines: list[str] = [SYSTEM_ROLE, "", _TOOL_TABLE_HEADER]
    lines.extend(f"- {spec.name}：{spec.description}" for spec in TOOL_SPECS)
    lines.extend(["", OUTPUT_PROTOCOL, "", _TIME_ANCHOR_NOTE])
    prompt = "\n".join(lines)
    tokens = estimator(prompt)
    if tokens > SYSTEM_PROMPT_TOKEN_LIMIT:
        msg = (
            f"系统提示 {tokens} tokens 超预算上限 {SYSTEM_PROMPT_TOKEN_LIMIT}；"
            "请精简模块级模板常量后再交付，勿放宽预算"
        )
        raise ValueError(msg)
    return prompt


def visible_hypotheses(
    session: InvestigationSession,
    *,
    threshold: int = DEFAULT_EVICTION_THRESHOLD,
    step_count: int | None = None,
) -> list[Hypothesis]:
    """主上下文窗口可见的假设集合（视图层过滤，不改 session.hypotheses）。

    步数 < threshold：全量可见；≥ threshold：rejected 移出窗口
    （session.hypotheses 全量保留，active/confirmed 不动——架构 §3.3 定值）。
    步数读取可注入（step_count），供 T6 主循环复用与 G8 判据 1 注入。
    """
    count = session.step_count if step_count is None else step_count
    if count < threshold:
        return list(session.hypotheses)
    return [h for h in session.hypotheses if h.status is not HypothesisStatus.REJECTED]


# D-37 定案键集（来源拆分）：前四取 labels，后四取 alert 本体；键集合一次到位冻结
_OPENING_LABEL_KEYS = ("alertname", "instance", "job", "severity")
_OPENING_ALERT_KEYS = ("source", "status", "fired_at", "last_fired_at")


def project_opening(opening: dict[str, Any] | None) -> dict[str, Any] | None:
    """D-17 卡片 → 开局锚点精简投影（D-37）：8 字段 + 三源 status 摘要。

    投影而非全卡片：context.items 与 steps 摘要重复、挤 token 预算；只补
    「服务名 + 时间窗 + 源可用性」这类开局锚点。卡片缺字段容缺 None，
    键集合稳定；时间字段沿用卡片内字符串原样（序列化口径在上游 views 层）。
    """
    if opening is None:
        return None
    alert = opening.get("alert") or {}
    labels = alert.get("labels") or {}
    projected: dict[str, Any] = {key: labels.get(key) for key in _OPENING_LABEL_KEYS}
    projected.update({key: alert.get(key) for key in _OPENING_ALERT_KEYS})
    sources = (opening.get("context") or {}).get("sources") or []
    projected["context_status"] = {source.get("source"): source.get("status") for source in sources}
    return projected


def build_decision_view(
    session: InvestigationSession, notices: list[str], opening: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Planner 决策视图（T5/D-37）：四键原样搬入 + `opening` 事件锚点键。

    键集合稳定（opening 缺省 None 时键仍存在）——MockPlanner script 回放
    与既有测试的隐性契约；notices 复制传入序列，不改调用方状态。
    """
    return {
        "system_prompt": build_system_prompt(),
        "steps": [summarize_step(step) for step in session.steps],
        "hypotheses": [h.text for h in visible_hypotheses(session)],  # D-27③ 复用 T4
        "notices": list(notices),
        "opening": project_opening(opening),
    }
