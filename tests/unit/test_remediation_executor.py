"""受控执行器单测（M5 issue 05 / T5 / G4 / D-42）。

全部走注入替身 runner（本票零真实 subprocess 外呼，真实容器执行归 issue 08）：
放行路径 argv 断言；白名单外拒绝 + 审计留痕（校验前后参数都在）；注入样本拒绝
（参数正则 + 禁 shell 双防线之 allowlist 侧）；30s 超时归类；输出限长截断；
审计含命令/参数/decision/ts。
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from oncall.remediation.executor import (
    DEFAULT_TIMEOUT_S,
    MAX_OUTPUT_BYTES,
    ControlledExecutor,
)


class FakeRunner:
    """subprocess.run 替身：记录调用并按脚本返回 CompletedProcess 形对象。"""

    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.calls: list[dict[str, Any]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append({"argv": argv, **kwargs})
        return type(
            "CompletedProcess",
            (),
            {
                "returncode": self.returncode,
                "stdout": self.stdout,
                "stderr": self.stderr,
            },
        )()


class HangingRunner(FakeRunner):
    """挂起替身：抛 TimeoutExpired 模拟 30s 超时。"""

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append({"argv": argv, **kwargs})
        raise subprocess.TimeoutExpired(cmd=argv[0], timeout=kwargs["timeout"])


def _dry_run(*commands: dict[str, Any]) -> dict[str, Any]:
    return {"runbook_slug": "cpu-spike", "action_id": "x", "commands": list(commands)}


def _remove_cmd(**overrides: Any) -> dict[str, Any]:
    cmd: dict[str, Any] = {
        "step": 1,
        "action": "docker.remove_container",
        "command": "docker.remove_container name=cpu-spike-probe",
        "impact": "停止并移除 demo 容器",
        "runtime_params": [],
        "params": {"name": "cpu-spike-probe"},
    }
    cmd.update(overrides)
    return cmd


class TestAllowPath:
    def test_allowlisted_command_runs_with_rendered_argv(self) -> None:
        """白名单内命令 → 替身 runner 收到渲染 argv（shell=False / timeout=30）。"""
        runner = FakeRunner(stdout=b"cpu-spike-probe\n")
        result = ControlledExecutor(runner=runner).execute(_dry_run(_remove_cmd()))
        assert len(runner.calls) == 1
        call = runner.calls[0]
        assert call["argv"] == ["docker", "rm", "-f", "cpu-spike-probe"]
        assert call["shell"] is False
        assert call["timeout"] == DEFAULT_TIMEOUT_S
        assert call["capture_output"] is True
        assert result["ok"] is True
        assert result["rejected"] == []
        assert result["executed"][0]["ok"] is True

    def test_runtime_var_injected_and_audited(self) -> None:
        """$var 执行期注入：runtime_values 提供后参数过正则，审计含前后参数。"""
        runner = FakeRunner()
        cmd = _remove_cmd(
            action="mysql.kill_session",
            runtime_params=["session_id"],
            params={"container": "oncall-demo-mysql-1", "session_id": "$session_id"},
        )
        result = ControlledExecutor(runner=runner, runtime_values={"session_id": "42"}).execute(
            _dry_run(cmd)
        )
        assert result["ok"] is True
        record = result["executed"][0]
        assert record["params_before"] == {
            "container": "oncall-demo-mysql-1",
            "session_id": "$session_id",
        }
        assert record["params_checked"]["session_id"] == "42"
        assert record["argv"][-1] == "KILL 42"

    def test_runtime_var_missing_rejected_with_audit(self) -> None:
        """$var 未注入 → 拒绝 + 审计留痕（runner 不被调用）。"""
        runner = FakeRunner()
        cmd = _remove_cmd(
            action="mysql.kill_session",
            runtime_params=["session_id"],
            params={"container": "oncall-demo-mysql-1", "session_id": "$session_id"},
        )
        result = ControlledExecutor(runner=runner).execute(_dry_run(cmd))
        assert result["ok"] is False
        assert runner.calls == []
        rejected = result["rejected"][0]
        assert rejected["decision"] == "reject"
        assert "未注入" in rejected["reason"]
        assert rejected["params_before"]["session_id"] == "$session_id"


class TestRejectPath:
    def test_unregistered_action_rejected_and_audited(self) -> None:
        """白名单外命令拒绝 + 审计留痕（校验前后参数都在）。"""
        runner = FakeRunner()
        bad = _remove_cmd(action="docker.rm", params={"name": "oncall-demo-api-gw-1"})
        result = ControlledExecutor(runner=runner).execute(_dry_run(bad))
        assert result["ok"] is False
        assert runner.calls == []
        rejected = result["rejected"][0]
        assert rejected["decision"] == "reject"
        assert "未登记" in rejected["reason"]
        assert rejected["params_before"] == {"name": "oncall-demo-api-gw-1"}
        assert rejected["params_checked"] == {"name": "oncall-demo-api-gw-1"}
        assert rejected["ts"]

    @pytest.mark.parametrize(
        "payload",
        ["; rm -rf /", "&& echo pwned", "| cat /etc/passwd", "$(whoami)", "`id`"],
    )
    def test_injection_samples_rejected_and_audited(self, payload: str) -> None:
        """注入样本（参数正则 + 元字符黑名单）拒绝 + 审计留痕，runner 零调用。"""
        runner = FakeRunner()
        result = ControlledExecutor(runner=runner).execute(
            _dry_run(_remove_cmd(params={"name": payload}))
        )
        assert runner.calls == []
        rejected = result["rejected"][0]
        assert rejected["decision"] == "reject"
        assert rejected["params_before"] == {"name": payload}
        assert rejected["ts"]

    def test_mixed_commands_partial_rejection(self) -> None:
        """一条拒绝一条放行：拒绝不抛异常，ok=False，两类审计齐全。"""
        runner = FakeRunner()
        result = ControlledExecutor(runner=runner).execute(
            _dry_run(_remove_cmd(params={"name": "bad; name"}), _remove_cmd(step=2))
        )
        assert result["ok"] is False
        assert [r["step"] for r in result["rejected"]] == [1]
        assert [e["step"] for e in result["executed"]] == [2]


class TestTimeoutAndLimits:
    def test_timeout_classified_as_failed_step(self) -> None:
        """替身挂起 → TimeoutExpired 归类为失败步（不抛出、审计留痕）。"""
        runner = HangingRunner()
        result = ControlledExecutor(runner=runner).execute(_dry_run(_remove_cmd()))
        assert result["ok"] is False
        record = result["executed"][0]
        assert record["ok"] is False
        assert "timeout" in record["error"]

    def test_output_truncated_to_limit(self) -> None:
        """stdout/stderr 各限长截断（≤4KB）。"""
        runner = FakeRunner(stdout=b"A" * (MAX_OUTPUT_BYTES + 100), stderr=b"B" * 10)
        result = ControlledExecutor(runner=runner).execute(_dry_run(_remove_cmd()))
        record = result["executed"][0]
        assert len(record["stdout"].encode()) <= MAX_OUTPUT_BYTES
        assert record["stderr"] == "B" * 10

    def test_nonzero_returncode_is_failed_step(self) -> None:
        runner = FakeRunner(returncode=1, stderr=b"boom")
        result = ControlledExecutor(runner=runner).execute(_dry_run(_remove_cmd()))
        assert result["ok"] is False
        assert result["executed"][0]["ok"] is False
