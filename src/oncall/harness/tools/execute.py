"""execute_action 干跑 handler（M5 issue 02 / T2 / G1+G3 / D-39 / D-41，裁决① dry-run 语义）。

推理与执行分离（D-39）：模型只做「请求处置」决策；批准对象 = 干跑渲染出的**具体命令清单**
（`proposal.dry_run_json`）。本 handler 是干跑模式——解析 `action` = `runbook_slug/action_id`
定位器 → 经注入的 runbook 库接缝定位 Runbook → 按 `.id` 命中 action → 渲染「将执行的命令
清单 + 影响面」（干跑预览）→ 经注入的 proposal 存根接缝产 `proposal_id` → 返回
`ToolResult{ok, data:{dry_run_preview, proposal_id}}`。

**本 handler 永不执行任何 demo 侧写操作**：零 subprocess、零容器操作、零 HTTP——干跑（第一
道闸门）只打印命令与影响面，实际执行归确认门/服务层（issue 03/04/05），不经 loop 工具。

命令字符串永不来自 runbook 正文或模型自由文本（D-42/C4）：清单以**白名单原子操作引用 +
参数模板**形态渲染（如 `docker.remove_container name=cpu-spike-probe`），具体 shell 命令
模板归 issue 05 白名单；参数模板 `$var`（issue 01 语义）在干跑期显式标注「运行时解析、执行
期注入」，不静默吞掉（裁决②）。

C3 硬约束：harness 静态面不 import `oncall.remediation`——handler 只经注入的 `runbook_loader`
接缝拿 Runbook 数据，类型仅本地结构 Protocol（无 TYPE_CHECKING remediation import），
import-linter 零新增 harness→remediation 边。装配点/测试负责调 `load_runbook_library` 组装 loader。

本票错误路径（D-16，不抛原始异常）：action 缺 `/` / runbook slug 不在库 / action_id 命中不到 /
装配缺失 → `ToolResult{status: error, meta:{reason}}`。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final, Protocol

from oncall.harness.tools.schemas import ExecuteActionInput, ToolResult, ToolStatus

if TYPE_CHECKING:
    from oncall.harness.tools.registry import ToolHandler

__all__ = ["ProposalCreator", "RunbookLoader", "build_execute_action_handler"]

# C8 合规：静态影响面措辞不可变（frozenset of tuples，随 D-42 白名单动作族增长扩展）。
# key = 白名单原子操作全名（对齐 runbook.ACTION_KEYS），value = demo 侧副作用一句话。
_ATOMIC_IMPACT: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        (
            "docker.remove_container",
            "停止并移除 demo 容器（探针/Pumba 等辅助容器，container 状态不可恢复需重建）",
        ),
        ("docker.restore_cpuset", "恢复 api-gw 容器原始 CPU 亲和（cpuset），解除核打满"),
        ("mysql.kill_session", "KILL demo MySQL 内持锁/睡眠会话以释放表锁（幂等）"),
    }
)

_VAR_PREFIX = "$"  # issue 01 定稿：`$var` 前缀 = 运行时解析变量
_RUNTIME_NOTE = "运行时解析，执行期注入"  # 裁决②：$var 参数在干跑期的显式标注


def _impact_for(action: str) -> str:
    """查静态影响面措辞；白名单外动作（防御，正常不会到）给保守兜底措辞。"""
    for key, text in _ATOMIC_IMPACT:
        if key == action:
            return text
    return f"demo 侧副作用：执行白名单原子操作 {action}（影响面由 issue 05 白名单扩展登记）"


def _is_runtime(value: object) -> bool:
    """判断参数值是否为运行时模板变量（`$var` 前缀，issue 01 语义）。"""
    return isinstance(value, str) and value.startswith(_VAR_PREFIX)


class RunbookLoader(Protocol):
    """runbook 库加载接缝：给定 slug 返回 Runbook 或 None（remediation 实现，经组装点注入）。

    harness 不 import remediation——这里只声明最小结构契约，remediation 的 `Runbook` 对象
    天然满足（.slug / .actions[].id / .actions[].steps[].action / .params）。
    """

    def __call__(self, slug: str) -> object | None: ...


class ProposalCreator(Protocol):
    """proposal 存根接缝：接收干跑载荷，返回 proposal_id 字符串。

    issue 03 的落库实现填充此接缝（建 `remediation_proposals` 行）；本票只定义接缝、
    不建表不写库。载荷携带 `dry_run_json`（= 干跑预览，issue 05/06 消费的锁定对象）。
    """

    def __call__(self, payload: dict[str, Any]) -> str: ...


def _render_command(step: Any) -> tuple[str, list[str]]:
    """渲染一步命令：`<原子操作> key=value ...`；`$var` 参数单独列出（不吞）。"""
    action = getattr(step, "action", "")
    params: Mapping[str, object] = dict(getattr(step, "params", None) or {})
    runtime: list[str] = []
    tokens: list[str] = [action]
    for key, value in params.items():
        if _is_runtime(value):
            runtime.append(key)
            tokens.append(f"{key}={value}")  # 保留模板变量原文，可见「待运行时注入」
        else:
            tokens.append(f"{key}={value}")
    return " ".join(tokens), runtime


def build_execute_action_handler(
    *,
    runbook_loader: RunbookLoader | None,
    proposal_creator: ProposalCreator | None,
) -> ToolHandler:
    """工厂：注入 runbook 库加载接缝 + proposal 存根接缝，返回 ToolHandler 签名执行函数。

    未注入任一接缝 → 干跑 handler 装配缺失返回 error（D-16，不抛）；「既有测试零回退」
    指 registry 侧未注入 execute_action 时维持 execute_action_stub（见 registry._STUB_HANDLERS）。
    """

    def handler(args: ExecuteActionInput, *, timeout_seconds: float) -> ToolResult:
        del timeout_seconds  # 干跑是只读渲染，无外部超时兑现面（issue 05 执行器才用）
        if runbook_loader is None or proposal_creator is None:
            return ToolResult(
                tool="execute_action",
                status=ToolStatus.ERROR,
                data=None,
                meta={
                    "reason": (
                        "execute_action 干跑 handler 装配缺失：需注入 runbook 库加载器"
                        "与 proposal 存根接缝"
                    )
                },
            )
        raw = args.action
        if "/" not in raw:
            return ToolResult(
                tool="execute_action",
                status=ToolStatus.ERROR,
                data=None,
                meta={
                    "reason": (
                        f"action 必须是 runbook_slug/action_id 定位器（缺 '/'）：{raw!r}；"
                        "如 cpu-spike/stop-stress-and-restore-cpuset"
                    )
                },
            )
        slug, action_id = raw.split("/", 1)
        runbook = runbook_loader(slug)
        if runbook is None:
            return ToolResult(
                tool="execute_action",
                status=ToolStatus.ERROR,
                data=None,
                meta={"reason": f"runbook slug {slug!r} 不在处置 runbook 库中"},
            )
        action = next(
            (a for a in getattr(runbook, "actions", []) if getattr(a, "id", None) == action_id),
            None,
        )
        if action is None:
            return ToolResult(
                tool="execute_action",
                status=ToolStatus.ERROR,
                data=None,
                meta={"reason": f"runbook {slug!r} 内不存在 action_id {action_id!r}"},
            )
        commands: list[dict[str, Any]] = []
        impacts: list[str] = []
        for step_no, step in enumerate(getattr(action, "steps", []), start=1):
            step_action = getattr(step, "action", "")
            command, runtime_params = _render_command(step)
            impact = _impact_for(step_action)
            commands.append(
                {
                    "step": step_no,
                    "action": step_action,
                    "command": command,
                    # issue 05 落点：原始参数模板 dict（含 $var 原文）随批准对象锁定
                    # （D-39），executor 执行期经 runtime_values 注入后过白名单正则；
                    # `command` 仍是给人看的预览，永不进入执行（D-42）。
                    "params": dict(getattr(step, "params", None) or {}),
                    "impact": impact,
                    "runtime_params": runtime_params,
                }
            )
            impacts.append(impact)
        preview: dict[str, Any] = {
            "runbook_slug": slug,
            "action_id": action_id,
            "action_name": getattr(action, "name", ""),
            "commands": commands,
            "impact": "；".join(impacts),
        }
        proposal_id = proposal_creator(
            {
                "runbook_slug": slug,
                "action_id": action_id,
                "dry_run_json": preview,
            }
        )
        return ToolResult(
            tool="execute_action",
            status=ToolStatus.OK,
            data={"dry_run_preview": preview, "proposal_id": proposal_id},
            meta={"runbook_slug": slug, "action_id": action_id, "status": "pending"},
        )

    return handler
