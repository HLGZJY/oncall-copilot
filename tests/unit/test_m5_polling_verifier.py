"""PollingRecoveryVerifier 单测（M5-08 harness 支撑，纯逻辑零 IO）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from m5_real_support import PollingRecoveryVerifier


class ScriptedInner:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = list(results)
        self.calls = 0

    def verify(self, dry_run_json: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0]


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def test_polls_until_recovered_then_returns_real_result():
    """未恢复按间隔继续问，判恢复即停；返回最后一次真实结果 + 次数注记。"""
    inner = ScriptedInner(
        [
            {"recovered": False, "observed": 25.0},
            {"recovered": False, "observed": 12.0},
            {"recovered": True, "observed": 0.01},
        ]
    )
    clock = Recorder()
    verifier = PollingRecoveryVerifier(
        inner, interval_s=15.0, deadline_s=240.0, sleep=clock.sleep, monotonic=clock.monotonic
    )
    out = verifier.verify({})
    assert inner.calls == 3
    assert clock.sleeps == [15.0, 15.0]
    assert out["recovered"] is True and out["observed"] == 0.01
    assert out["poll_attempts"] == 3


def test_deadline_returns_last_real_result_never_fabricates():
    """超时返回最后一次真实结果（不伪造 recovered）；deadline 用尽即停。"""
    inner = ScriptedInner([{"recovered": False, "observed": 25.0, "error": "x"}])
    clock = Recorder()
    verifier = PollingRecoveryVerifier(
        inner, interval_s=15.0, deadline_s=30.0, sleep=clock.sleep, monotonic=clock.monotonic
    )
    out = verifier.verify({})
    # t=0 第 1 次 → sleep15（t=15）第 2 次 → sleep15（t=30）达 deadline 前再问 1 次
    assert inner.calls == 3
    assert out["recovered"] is False and out["error"] == "x"
    assert out["poll_attempts"] == 3


def test_first_attempt_recovered_no_sleep():
    """首问即恢复：零等待、单次回查（窗口恰已干净的正常路径）。"""
    inner = ScriptedInner([{"recovered": True, "observed": 0.01}])
    clock = Recorder()
    verifier = PollingRecoveryVerifier(
        inner, interval_s=15.0, deadline_s=240.0, sleep=clock.sleep, monotonic=clock.monotonic
    )
    out = verifier.verify({})
    assert inner.calls == 1 and clock.sleeps == []
    assert out["recovered"] is True and out["poll_attempts"] == 1
