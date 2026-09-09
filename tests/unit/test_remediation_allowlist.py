"""命令白名单单测（M5 issue 05 / T5 / G4 / D-42）。

覆盖：白名单内命令/参数放行（argv 渲染断言）；白名单外动作拒绝；参数缺失/
多余/不匹配拒绝；shell 元字符注入样本逐个拒绝（allowlist 层防线一：参数正则
+ 元字符黑名单；防线二「禁 shell」在 executor/架构守卫层，缺一不算过验收③）。
"""

from __future__ import annotations

from typing import Any

import pytest

from oncall.remediation.allowlist import ATOMIC_ACTIONS, AllowlistViolation, render_argv


class TestRenderAllowlisted:
    def test_docker_remove_container_rendered(self) -> None:
        """cpu-spike 步骤：docker.remove_container → docker rm -f <name>。"""
        argv = render_argv("docker.remove_container", {"name": "cpu-spike-probe"})
        assert argv == ["docker", "rm", "-f", "cpu-spike-probe"]

    def test_docker_restore_cpuset_rendered(self) -> None:
        """cpu-spike 步骤：docker.restore_cpuset → docker update --cpuset-cpus=...。"""
        argv = render_argv(
            "docker.restore_cpuset",
            {"container": "oncall-demo-api-gw-1", "cores": "0-3"},
        )
        assert argv == [
            "docker",
            "update",
            "--cpuset-cpus=0-3",
            "oncall-demo-api-gw-1",
        ]

    def test_mysql_kill_session_rendered(self) -> None:
        """slow-sql 步骤：mysql.kill_session → KILL 语句整体为单个 argv 元素。"""
        argv = render_argv(
            "mysql.kill_session",
            {"container": "oncall-demo-mysql-1", "session_id": "42"},
        )
        assert argv == [
            "docker",
            "exec",
            "oncall-demo-mysql-1",
            "mysql",
            "-uroot",
            "-e",
            "KILL 42",
        ]

    def test_table_covers_runbook_two_families(self) -> None:
        """原子操作表覆盖 runbook 定稿的 docker/mysql 两族三动作（D-47）。"""
        assert set(ATOMIC_ACTIONS) == {
            "docker.remove_container",
            "docker.restore_cpuset",
            "mysql.kill_session",
        }


class TestReject:
    def test_unregistered_action_rejected(self) -> None:
        """白名单外动作（任意 docker 命令变体）拒绝。"""
        with pytest.raises(AllowlistViolation, match="未登记"):
            render_argv("docker.rm", {"name": "cpu-spike-probe"})
        with pytest.raises(AllowlistViolation, match="未登记"):
            render_argv("bash.exec", {"cmd": "anything"})

    def test_missing_param_rejected(self) -> None:
        with pytest.raises(AllowlistViolation, match="参数缺失"):
            render_argv("docker.remove_container", {})

    def test_extra_param_rejected(self) -> None:
        """白名单外多余参数拒绝（严格最小面）。"""
        with pytest.raises(AllowlistViolation, match="白名单外参数"):
            render_argv(
                "docker.remove_container",
                {"name": "cpu-spike-probe", "force": "1"},
            )

    def test_mismatched_param_rejected(self) -> None:
        """参数不匹配白名单正则拒绝。"""
        with pytest.raises(AllowlistViolation, match="不匹配"):
            render_argv("mysql.kill_session", {"container": "ok", "session_id": "abc"})
        with pytest.raises(AllowlistViolation, match="不匹配"):
            render_argv("docker.remove_container", {"name": "A" * 100})

    @pytest.mark.parametrize(
        "payload",
        ["; rm -rf /", "&& echo pwned", "| cat /etc/passwd", "$(whoami)", "`id`"],
    )
    def test_shell_metachar_injection_samples_rejected(self, payload: str) -> None:
        """注入样本（;/&&/|/$()/反引号）逐个拒绝，拒绝原因点明元字符。"""
        with pytest.raises(AllowlistViolation, match="元字符"):
            render_argv("docker.remove_container", {"name": payload})

    def test_render_is_pure(self) -> None:
        """纯函数：渲染不改变入参（零 IO/零 subprocess 是模块纪律）。"""
        params: dict[str, Any] = {"name": "cpu-spike-probe"}
        render_argv("docker.remove_container", params)
        assert params == {"name": "cpu-spike-probe"}
