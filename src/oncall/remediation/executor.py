"""受控执行器（M5 issue 04 接缝 + issue 05 实装 / T4+T5 / G4 / D-42 / D-48）。

issue 04 定义注入接缝（结构协议，照 issue 02 `ProposalCreator` 先例）；
issue 05 在同文件落真实实现。api/remediation 只经 deps 拿实例，不感知实现。

D-39 语义：执行器收到的唯一输入 = `proposal.dry_run_json`（批准对象）——
api 层与状态机都不改写它，执行清单从批准到执行零漂移（设计风险 4 收口）。

issue 05 实装要点（G4/D-42/R4/A6）：
- **命令源收口**：唯一可执行面 = `allowlist.render_argv` 渲染产物；
  dry_run_json 的 `command` 字段是渲染给人看的预览，**永不进入执行**。
- **subprocess 注入接缝**：runner 默认 `subprocess.run(argv, shell=False,
  timeout=30, capture_output=True)`；测试注入替身断言收到的 argv；
  真实容器执行与 create_app 装配归 issue 08 统一收口。
- **$var 运行时参数**：构造时注入 `runtime_values`（执行期注入，issue 02
  裁决②的落点）；未注入的运行时参数 → 白名单拒绝 + 审计留痕。
- **白名单拒绝不抛异常**：拒绝也进审计并体现在返回 dict（api 层不感知
  白名单细节）。
- **返回 dict 形状**（落 `params_json["execution"]`，api 层消费）：
  `{"executed": [...], "rejected": [...], "ok": bool, "output_summary": str}`；
  每条审计记录含 原子操作名 / argv（放行时）/ 校验前后参数 / decision / ts。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from oncall.remediation.allowlist import AllowlistViolation, render_argv

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MAX_OUTPUT_BYTES",
    "ControlledExecutor",
    "RemediationExecutor",
]

# 30s 超时（替身挂起 → TimeoutExpired 归类为失败步）；输出限长 stdout/stderr 各 4KB
DEFAULT_TIMEOUT_S: Final[float] = 30.0
MAX_OUTPUT_BYTES: Final[int] = 4096


class RemediationExecutor(Protocol):
    """受控执行器接缝：消费 dry_run_json 命令清单，返回执行输出摘要。

    返回 dict 落 `proposal.params_json["execution"]`（D-46 行内执行留痕载体，
    GET 处置查询的「执行输出摘要」来源）；issue 05 实装后键形状随实现细化。
    """

    def execute(self, dry_run_json: dict[str, Any]) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _truncate(data: bytes | str) -> str:
    """输出限长：超 MAX_OUTPUT_BYTES 截断（bytes 先切再解码，避免多字节撕裂）。"""
    if isinstance(data, str):
        data = data.encode()
    return data[:MAX_OUTPUT_BYTES].decode(errors="replace")


class ControlledExecutor:
    """真实受控执行器（满足 `RemediationExecutor` Protocol）。

    流程：解析 `dry_run_json["commands"]` → `$var` 运行时注入 → 逐条经
    allowlist 校验渲染 argv → **仅接受白名单校验后的命令** → 执行并审计。
    """

    def __init__(
        self,
        *,
        runner: Callable[..., Any] | None = None,
        runtime_values: Mapping[str, str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._runner = runner if runner is not None else self._default_runner
        self._runtime_values: dict[str, str] = dict(runtime_values or {})
        self._timeout_s = timeout_s

    @staticmethod
    def _default_runner(argv: list[str], **kwargs: Any) -> Any:
        # S603 由「argv 仅来自白名单渲染 + shell=False + 架构守卫 test_no_shell_true」
        # 三重看管（A6）；check=False 是刻意的——执行结果按 returncode 审计而非抛出
        return subprocess.run(argv, **kwargs, check=False)  # noqa: S603

    def _resolve_runtime(self, params: Mapping[str, Any]) -> dict[str, Any] | str:
        """`$var` 执行期注入；缺注入返回拒绝原因字符串（裁决②，不静默吞掉）。"""
        resolved: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("$"):
                var = value[1:]
                if var not in self._runtime_values:
                    return f"运行时参数 {value} 未注入（执行期注入缺失）"
                resolved[key] = self._runtime_values[var]
            else:
                resolved[key] = value
        return resolved

    def execute(self, dry_run_json: dict[str, Any]) -> dict[str, Any]:
        """消费批准对象命令清单，返回执行审计摘要（形状见模块 docstring）。"""
        executed: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for cmd in dry_run_json.get("commands") or []:
            action = str(cmd.get("action", ""))
            before = dict(cmd.get("params") or {})
            resolved = self._resolve_runtime(before)
            record: dict[str, Any] = {
                "step": cmd.get("step"),
                "action": action,
                "params_before": before,
                "params_checked": resolved if isinstance(resolved, dict) else {},
                "decision": "allow",
                "ts": _now(),
            }
            if isinstance(resolved, str):  # $var 未注入 → 拒绝留痕
                record.update(decision="reject", reason=resolved, argv=None)
                rejected.append(record)
                continue
            try:
                argv = render_argv(action, resolved)
            except AllowlistViolation as exc:  # 白名单拒绝：进审计不抛出
                record.update(decision="reject", reason=str(exc), argv=None)
                rejected.append(record)
                continue
            record["argv"] = argv
            try:
                proc = self._runner(argv, shell=False, timeout=self._timeout_s, capture_output=True)
            except subprocess.TimeoutExpired:
                record.update(
                    ok=False,
                    error=f"timeout>{self._timeout_s:.0f}s",
                    returncode=None,
                    stdout="",
                    stderr="",
                )
                executed.append(record)
                continue
            record.update(
                ok=getattr(proc, "returncode", 1) == 0,
                returncode=getattr(proc, "returncode", None),
                stdout=_truncate(getattr(proc, "stdout", None) or b""),
                stderr=_truncate(getattr(proc, "stderr", None) or b""),
            )
            executed.append(record)
        ok = not rejected and all(item["ok"] for item in executed)
        summary = (
            f"共 {len(executed) + len(rejected)} 条：allow {len(executed)}"
            f"（ok {sum(1 for item in executed if item['ok'])}）/ reject {len(rejected)}"
        )
        return {
            "executed": executed,
            "rejected": rejected,
            "ok": ok,
            "output_summary": summary,
        }
