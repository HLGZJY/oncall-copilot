"""M5-08 真实 e2e harness 支撑（观察窗轮询 + 组件组装 + chaos/HTTP 驱动）。

真实 e2e 的「真实面」= 四道闸门管线在活 demo 栈的行为：真实受控执行器
（白名单 argv → docker/mysql CLI）、真实恢复验证器（Prometheus 回查）、
真实确认门 HTTP（uvicorn + curl）。调查面按 M5 纪律仍是 mock 决策脚本
（零 LLM）。生产代码零改动——观察窗「回看 60s 全样本满足判据」在执行后
立即回查必含故障期样本（必假未恢复），故验证器外层包**轮询代理**：真实
判据与判定全部来自内层真实验证器，代理只决定「何时再问一次」（G2 confirm
预算本就含验证窗口 ≤60s，预算放大到 240s 覆盖首验失败后的窗口滚动）。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import uvicorn

from oncall.remediation.verifier import RecoveryVerifier

__all__ = [
    "LIVE_PROM_URL",
    "LiveServer",
    "PollingRecoveryVerifier",
    "curl_json",
    "docker_inspect_cpuset",
    "mysql_lock_session_id",
    "prom_instant",
    "run_bash",
    "wait_alert_firing",
]

LIVE_PROM_URL = "http://127.0.0.1:9090"
REPO_ROOT = Path(__file__).resolve().parents[2]


class PollingRecoveryVerifier:
    """恢复验证器轮询代理（满足 `RecoveryVerifier`）。

    内层真实验证器逐次回查 Prometheus 观察窗；未恢复则等一个采样间隔再问，
    直到判恢复或超时（返回最后一次真实结果，不伪造 recovered）。额外注记
    `poll_attempts`（真实回查次数）供实测回填「恢复窗口」。
    """

    def __init__(
        self,
        inner: RecoveryVerifier,
        *,
        interval_s: float = 15.0,
        deadline_s: float = 240.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._interval_s = interval_s
        self._deadline_s = deadline_s
        self._sleep = sleep
        self._monotonic = monotonic

    def verify(self, dry_run_json: dict[str, Any]) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        deadline = self._monotonic() + self._deadline_s
        while True:
            result = self._inner.verify(dry_run_json)
            attempts.append(result)
            if result.get("recovered") or self._monotonic() >= deadline:
                break
            self._sleep(self._interval_s)
        out = dict(attempts[-1])
        out["poll_attempts"] = len(attempts)
        return out


# ---------------------------------------------------------------------------
# 活栈驱动：Prometheus 查询 / bash 剧本 / docker / mysql / curl
# ---------------------------------------------------------------------------


def prom_instant(fetcher: Any, query: str) -> float | None:
    """Prometheus 即时查询（活栈预检与参考值取数；None = 无样本）。"""
    resp = fetcher.get(f"{LIVE_PROM_URL}/api/v1/query", params={"query": query}, timeout=5.0)
    result = resp.json()["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def wait_alert_firing(fetcher: Any, alertname: str, *, timeout_s: float = 180.0) -> float:
    """轮询等待锚定告警 firing（for:1m + 抓取/评估延迟），返回等待秒数。"""
    started = time.monotonic()
    query = f'ALERTS{{alertname="{alertname}",alertstate="firing"}}'
    while time.monotonic() - started < timeout_s:
        if prom_instant(fetcher, query) is not None:
            return round(time.monotonic() - started, 1)
        time.sleep(5.0)
    raise AssertionError(f"告警 {alertname} 在 {timeout_s}s 内未 firing（注入或告警链路异常）")


def _git_bash() -> str:
    """定位 Git Bash（System32 的 bash.exe 是 WSL 入口，须避开）。"""
    candidates = [
        os.environ.get("ONCALL_BASH", ""),
        r"C:\Program Files\Git\bin\bash.exe",
        r"E:\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise AssertionError("未找到 Git Bash（可经 ONCALL_BASH 指定）")


def run_bash(script: Path, *, env_extra: Mapping[str, str] | None = None) -> str:
    """跑 chaos 注入/清理脚本（Git Bash + 环境变量注入，失败即断言）。"""
    env = {**os.environ, **(env_extra or {})}
    proc = subprocess.run(
        [_git_bash(), script.as_posix()],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"{script.name} 失败：{proc.stderr}"
    return proc.stdout


def docker_inspect_cpuset(container: str) -> str:
    """注入前读原始 cpuset（'' = 未收窄，调用方按 NCPU 兜底）。"""
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.HostConfig.CpusetCpus}}", container],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def docker_info_ncpu() -> int:
    proc = subprocess.run(
        ["docker", "info", "-f", "{{.NCPU}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return int(proc.stdout.strip())


def mysql_lock_session_id(container: str) -> str:
    """发现 tasks 表持锁会话（SELECT SLEEP 的注入会话），processlist 实查。

    必须排除自身连接（CONNECTION_ID()）：发现 SQL 的 info 自身含
    '%SLEEP(%'，会把本连接也匹配进去——T8 真实 e2e 实测踩坑：ids[0] 取到
    发现查询自己（查完即退出），KILL 报 Unknown thread id。
    """
    sql = (
        "SELECT id FROM information_schema.processlist"
        " WHERE command='Query' AND info LIKE '%SLEEP(%' AND id != CONNECTION_ID()"
    )
    proc = subprocess.run(
        ["docker", "exec", container, "mysql", "-uroot", "-poncall", "-N", "-e", sql],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    ids = [line.strip() for line in proc.stdout.splitlines() if line.strip().isdigit()]
    assert ids, "未发现持锁会话（注入会话已退出？）"
    return ids[0]


def curl_json(method: str, url: str, payload: Mapping[str, Any] | None = None) -> Any:
    """真实 HTTP 经 curl（确认门人工模拟面；confirm 同步链最长 ~5min）。"""
    cmd = ["curl", "-sS", "-X", method, url]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=500, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class LiveServer:
    """uvicorn 真实 HTTP 服务器（后台线程，127.0.0.1；真实 socket 面）。"""

    def __init__(self, app: Any, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started:
            assert time.monotonic() < deadline, "uvicorn 15s 内未就绪"
            time.sleep(0.1)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)
