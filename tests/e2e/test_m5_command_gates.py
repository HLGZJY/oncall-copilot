"""M5-T7 验收断言收口 · 命令白名单与 runbook 拒载（m5 issue 07 / 第 3 门收口）。

验收「命令白名单拒绝越权」（硬规 3 + D-42/R4/A6）的跨票收口断言：
- shell 注入样本 / 白名单外动作 / 多余参数 → 执行器层拒绝并留审计，零执行
- runbook 引用白名单外原子操作 → 拒绝加载（脏 runbook 进不了执行面）
部件级覆盖归 issue 01（test_remediation_runbook）/ issue 05
（test_remediation_allowlist + test_remediation_executor）单测，此处不重写。

零 LLM、零真实外呼、零 subprocess 真实执行（runner 替身）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from oncall.remediation.executor import ControlledExecutor
from oncall.remediation.runbook import RunbookValidationError, parse_runbook


def test_allowlist_rejects_injection_samples_with_audit():
    """白名单外动作 / shell 元字符 / 多余参数 → 执行器层拒绝并留审计，零执行。"""
    runner_calls: list[list[str]] = []

    def runner(argv: list[str], **kw: object) -> SimpleNamespace:
        runner_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    executor = ControlledExecutor(runner=runner)
    result = executor.execute(
        {
            "commands": [
                {"step": 1, "action": "docker.remove_container", "params": {"name": "x; rm -rf /"}},
                {
                    "step": 2,
                    "action": "docker.remove_container",
                    "params": {"name": "ok", "extra": "y"},
                },  # 白名单外多余参数
                {"step": 3, "action": "docker.exec", "params": {"container": "a"}},  # 白名单外动作
                {
                    "step": 4,
                    "action": "mysql.kill_session",
                    "params": {"container": "c", "session_id": "1 | nc evil"},
                },  # 元字符注入
            ]
        }
    )
    assert result["ok"] is False
    assert not result["executed"] and len(result["rejected"]) == 4
    assert runner_calls == []  # 越权样本零触达 runner
    for record in result["rejected"]:
        assert record["decision"] == "reject" and record["reason"] and record["argv"] is None
        assert record["params_before"] and record["ts"]  # 审计含校验前后参数


def test_runbook_referencing_unknown_action_rejected_on_load():
    """runbook action / rollback 引用白名单外原子操作 → 拒绝加载（fail-closed）。"""
    base = (
        "---\nslug: bad-rb\nalert_ref: X\nseverity: warning\n"
        "actions:\n  - id: a1\n    name: 动作\n    steps:\n"
    )
    tail = "rollback: []\nverification: {promql: up, condition: 'v > 0', window_s: 60}\n"
    with pytest.raises(RunbookValidationError):  # action 引用白名单外动作
        parse_runbook(base + "      - action: docker.exec\n        params: {container: c}\n" + tail)
    with pytest.raises(RunbookValidationError):  # rollback 引用白名单外动作
        parse_runbook(
            base
            + "      - action: mysql.kill_session\n"
            + "        params: {container: c, session_id: '1'}\n"
            + "rollback:\n  - action: docker.exec\n    params: {container: c}\n"
            + tail
        )
