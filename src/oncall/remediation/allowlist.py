"""命令白名单（M5 issue 05 / T5 / G4 / D-42）。

**静态原子操作表 = 系统层唯一可执行面**（G4/D-42）：动作类型 → 受限命令
argv 模板 + 参数白名单正则。runbook action 只可引用表内原子操作，命令字符串
永不来自 runbook 正文或模型自由文本——能变成 argv 的只有本模块「模板 +
校验后参数」渲染这一条路（R4/OWASP：命令对白名单校验、参数白名单正则、
禁 shell 拼接）。

纯函数、零 IO、零 subprocess：白名单只做「判定 + 渲染」，执行归 executor
（同票）；初始两族以 runbook 定稿为准（D-47）——docker 族
（remove_container / restore_cpuset）+ mysql 族（kill_session，幂等）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

__all__ = ["ATOMIC_ACTIONS", "AllowlistViolation", "render_argv"]

# 参数白名单正则（全锚定；形状对齐 runbook 定稿：容器名 / CPU 核列表 / 会话 id）
_CONTAINER_RE: Final[str] = r"[a-z0-9][a-z0-9_-]{0,63}"
_CORES_RE: Final[str] = r"[0-9]{1,3}(?:-[0-9]{1,3})?(?:,[0-9]{1,3}(?:-[0-9]{1,3})?){0,15}"
_SESSION_ID_RE: Final[str] = r"[0-9]{1,10}"

# shell 元字符黑名单（防御纵深：正则已收窄，这里给注入样本明确的拒绝原因）
_SHELL_META_CHARS: Final[frozenset[str]] = frozenset(";&|`$()<>\\'\"\n\r")

# 静态原子操作表（D-42/G4 定稿）：动作名 → (argv 模板, 参数 → 白名单正则)。
# argv 模板内 {param} 占位符由**校验后**参数替换；KILL 语句整体作为单个 argv
# 元素传给 docker exec（executor 侧 shell=False 列表参数，A6）。
ATOMIC_ACTIONS: Final[dict[str, tuple[tuple[str, ...], dict[str, str]]]] = {
    "docker.remove_container": (
        ("docker", "rm", "-f", "{name}"),
        {"name": _CONTAINER_RE},
    ),
    "docker.restore_cpuset": (
        ("docker", "update", "--cpuset-cpus={cores}", "{container}"),
        {"cores": _CORES_RE, "container": _CONTAINER_RE},
    ),
    "mysql.kill_session": (
        # -poncall：demo 栈 root 凭据与 chaos 脚本一致——T8 真实 e2e 实测发现
        # 缺密码时 mysql 客户端 Access denied（rc=1），mock runner 测不出真实认证
        ("docker", "exec", "{container}", "mysql", "-uroot", "-poncall", "-e", "KILL {session_id}"),
        {"container": _CONTAINER_RE, "session_id": _SESSION_ID_RE},
    ),
}


class AllowlistViolation(Exception):
    """白名单拒绝（未登记动作 / 参数缺失或多余 / 不匹配 / 含 shell 元字符）。"""


def _check_value(action: str, key: str, raw: Any) -> str:
    """单参数防线一：元字符黑名单 → 白名单正则；任一不过即拒绝。"""
    value = str(raw)
    hit = sorted(ch for ch in set(value) if ch in _SHELL_META_CHARS)
    if hit:
        msg = f"参数 {key} 含 shell 元字符 {hit!r}（注入样本拒绝）"
        raise AllowlistViolation(msg)
    pattern = ATOMIC_ACTIONS[action][1][key]
    if re.fullmatch(pattern, value) is None:
        msg = f"参数 {key}={value!r} 不匹配白名单正则 {pattern!r}"
        raise AllowlistViolation(msg)
    return value


def render_argv(action: str, params: Mapping[str, Any]) -> list[str]:
    """原子操作名 + 参数 dict → 逐参数过白名单 → 渲染后的 argv 列表。

    任何拒绝（动作未登记 / 参数缺失 / 多余 / 不匹配 / 元字符注入）抛
    AllowlistViolation；调用方（executor 审计 / runbook 引用对账）捕获后落
    decision=reject 留痕，不让拒绝信息静默消失。
    """
    spec = ATOMIC_ACTIONS.get(action)
    if spec is None:
        msg = f"原子操作 {action!r} 未登记于命令白名单（D-42 静态原子操作表）"
        raise AllowlistViolation(msg)
    template, patterns = spec
    missing = sorted(key for key in patterns if key not in params)
    if missing:
        msg = f"原子操作 {action!r} 参数缺失：{missing}"
        raise AllowlistViolation(msg)
    extra = sorted(key for key in params if key not in patterns)
    if extra:
        msg = f"原子操作 {action!r} 收到白名单外参数：{extra}"
        raise AllowlistViolation(msg)
    checked = {key: _check_value(action, key, params[key]) for key in patterns}
    return [token.format(**checked) for token in template]
