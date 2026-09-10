"""ContextManager：上下文预算管理（架构 §3.3 / §3.2，D-25/D-27③）。

职责边界（架构 §3.2）：只做**预算与摘要**，不生成计划、不改写工具语义。

- 摘要模板固定：组件/指标/异常方向/时间窗四要素齐全、同输入同输出
  （确定性——压缩 KV cache 重复前缀的前提）；变量缺失用确定性占位 `n/a`
- 工具输出预算：单次 ≤2000 tokens 只截「进上下文的摘要」，原始
  `output_json` 完整落 session（G4 指针语义：M3 = EvidenceStep 对象引用，
  M4 建表后替换为行 id，接口不变）；指针与 Registry `_summarize` 同语义
- 系统提示预算：≤1500 tokens——角色 + 工具一览（从 `TOOL_SPECS` 生成，
  勿手抄第二份清单）+ **工具入参 schema 摘要**（D-38：派生自注册契约的
  pydantic model_fields，与运行时校验同源防漂移）+ 输出协议（D-22）
- 步数 ≥10 移出已证伪假设：**视图层过滤**（组件 frozen 不可变，踩坑④），
  `session.hypotheses` 全量保留；步数阈值与步数读取可注入（G8 判据 1）

token 估算双口径说明：Registry 管工具输出截断用「字符数 ÷4」（保守）；
本模块默认「2 字符 ≈ 1 token」（票面 G8 / M2 同款粗估口径）——估算函数
可注入替换，两口径并存不改 Registry（冻结文件，冲突先在 issue 注记记录）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, get_args, get_origin

from annotated_types import Ge, Le, MaxLen, MinLen
from pydantic.fields import FieldInfo

from oncall.harness.session import HypothesisStatus
from oncall.harness.tools.registry import TOOL_SPECS, ToolSpec

if TYPE_CHECKING:
    from oncall.harness.session import EvidenceStep, Hypothesis, InvestigationSession

__all__ = [
    "DEFAULT_EVICTION_THRESHOLD",
    "SUMMARY_TEMPLATE",
    "SYSTEM_PROMPT_TOKEN_LIMIT",
    "TOKEN_BUDGET",
    "TOOL_SCHEMA_HEADER",
    "build_decision_view",
    "build_system_prompt",
    "estimate_tokens",
    "render_tool_schema",
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

_TOOL_TABLE_HEADER = "## 工具一览（名称：一句话说明）"

# D-38：schema 摘要标题——静态模板 A2 落位（模块级常量）；正文从注册契约派生，勿手抄
TOOL_SCHEMA_HEADER = "## 工具入参 schema（必填 / 可选 / 值域，派生自注册契约与运行时校验同源）"

OUTPUT_PROTOCOL = """\
## 输出协议（两分支互斥，thought 恒必填，只输出一个 JSON 对象）
- 继续调查（选工具）：{"thought": "<一句话依据>", "next_tool": "<工具名>", "args": {<入参>}}
- 证据已足够（收束）：{"thought": "<一句话依据>", "conclusion": "<结论与依据>"}
（两分支都必带 thought；收束分支禁止再带 next_tool/args，选工具分支禁止带 conclusion）"""

_TIME_ANCHOR_NOTE = (
    "时间窗缺省口径：以告警 last_fired_at 为锚取近期窗口（查「告警发生时」的状态）。"
)

# M3 issue 09（M7 issue 07 全 miss 复盘）：取证策略进系统提示——与 M6-T5
# 知识污染防线对齐而非绕过：KB-only 假设会被 Verifier 按设计拦截。
FORENSICS_FIRST_STRATEGY = """\
## 取证策略（先取证后 KB）
- 首轮必须先消费事件锚点（opening.event_anchors 告警时间线）与取证工具输出
  （query_metrics / search_logs / detect_anomaly / get_topology），禁止开局直接 query_kb；
- query_kb 降级为取证后参考召回：只在已有本源取证证据后用于对照历史相似案例；
- 禁止提出仅由 query_kb 支撑的假设——kb 是历史参考非当前事实，仅 KB 支撑的
  假设会被裁决层拦截；假设必须有取证证据步支撑；
- 同一工具+同一参数只允许调用一次：重复调用会触发警告并最终熔断转人工；
  需要新信息就换工具、换指标或换时间窗；
- 取证证据（异常方向 / 时间窗 / 服务与告警清单）已能解释告警时**立即收束**：
  基于已有证据给出结论，允许带不确定性表述；不要为「更确定」无限继续取证。\
"""


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
    指标 = 各工具主查询参数按序取第一个非空（`query` / `promql` /
    `selector`，M3 issue 09：此前只认 `query`，query_metrics/search_logs
    步骤摘要恒 n/a，planner 无法分辨已查内容→同参重复烧步数）；
    异常方向 = `output_json["direction"]`；时间窗 = start/end。缺失一律 n/a。
    """
    start = _element(step.input_json, "start")
    end = _element(step.input_json, "end")
    window = NA_PLACEHOLDER if NA_PLACEHOLDER in (start, end) else f"[{start} ~ {end}]"
    metric = NA_PLACEHOLDER
    for key in ("query", "promql", "selector"):
        if step.input_json.get(key):
            metric = str(step.input_json[key])
            break
    return SUMMARY_TEMPLATE.format(
        component=_element(step.input_json, "component"),
        metric=metric,
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


def _field_hints(field: FieldInfo) -> str:
    """值域约束提示（D-38 同源约束）：从 pydantic 字段元数据派生，勿手抄字符串。"""
    hints: list[str] = []
    for meta in field.metadata:
        if isinstance(meta, Ge):
            hints.append(f"≥{meta.ge}")
        elif isinstance(meta, Le):
            hints.append(f"≤{meta.le}")
        elif isinstance(meta, MinLen):
            hints.append(f"长度≥{meta.min_length}")
        elif isinstance(meta, MaxLen):
            hints.append(f"长度≤{meta.max_length}")
    if get_origin(field.annotation) is Literal:  # 枚举值域（如 backward|forward）
        hints.append("|".join(str(v) for v in get_args(field.annotation)))
    return ",".join(hints)


def _render_field(name: str, field: FieldInfo) -> str:
    """单参数渲染：`名?` 可选标记 + 值域括注 + 契约 description 透出。"""
    mark = "" if field.is_required() else "?"
    inner = _field_hints(field)
    token = f"{name}{mark}({inner})" if inner else f"{name}{mark}"
    return f"{token}——{field.description}" if field.description else token


def render_tool_schema(spec: ToolSpec) -> str:
    """工具入参 schema 摘要（D-38）：必填 / 可选两段，各工具 2 行（无可选则 1 行）。

    从 `spec.schema.model_fields` 派生——与 ToolRegistry 运行时校验同一数据源，
    文档 schema 与校验永不漂移；排序按模型字段声明序（确定性输出）。
    """
    required: list[str] = []
    optional: list[str] = []
    for name, field in spec.schema.model_fields.items():
        token = _render_field(name, field)
        (required if field.is_required() else optional).append(token)
    head = f"- {spec.name}：必填 {'、'.join(required) if required else '无'}"
    return f"{head}\n  可选 {'、'.join(optional)}" if optional else head


def build_system_prompt(estimator: TokenEstimator = estimate_tokens) -> str:
    """组装系统提示：角色 + 六工具一览 + 入参 schema 摘要（D-38）+ 输出协议（D-22）。

    组装后整体断言 ≤1500 tokens（架构 §3.3，D-38 随票钉死）；超限属模板膨胀
    事故，直接抛错——先压缩一句话描述，不砍 schema 本身（缺口②主修复）。
    """
    lines: list[str] = [SYSTEM_ROLE, "", _TOOL_TABLE_HEADER]
    lines.extend(f"- {spec.name}：{spec.description}" for spec in TOOL_SPECS)
    lines.extend(["", TOOL_SCHEMA_HEADER])
    lines.extend(render_tool_schema(spec) for spec in TOOL_SPECS)
    lines.extend(["", OUTPUT_PROTOCOL, "", FORENSICS_FIRST_STRATEGY, "", _TIME_ANCHOR_NOTE])
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
    # M3 issue 09：事件锚点（告警时间线，复用 eval/evidence.py timeline 形态）
    # 按需透传——D-17 卡片不带此键时投影键集合不变（D-37 契约稳定）。
    if "event_anchors" in opening:
        projected["event_anchors"] = opening["event_anchors"]
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
