"""上下文拉取配置：窗口 / URL / 超时全走配置，不硬编码（issue 04 要点、D-16）。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

PROMETHEUS_URL_ENV = "ONCALL_PROMETHEUS_URL"
WINDOW_SECONDS_ENV = "ONCALL_CONTEXT_WINDOW_SECONDS"
TIMEOUT_SECONDS_ENV = "ONCALL_PROM_TIMEOUT_SECONDS"

DEFAULT_PROMETHEUS_URL = "http://127.0.0.1:9090"  # M0 compose 的 Prometheus 映射口
DEFAULT_WINDOW = timedelta(minutes=30)  # 「近期指标」的近期口径（issue 04 定值）
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_STEP = "30s"  # 序列采样步长，跟随窗口量级


@dataclass(frozen=True)
class ContextConfig:
    """三源拉取参数；窗口默认 30m，环境变量可覆盖。"""

    prometheus_url: str = DEFAULT_PROMETHEUS_URL
    window: timedelta = DEFAULT_WINDOW
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    step: str = DEFAULT_STEP

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ContextConfig:
        """环境变量加载；缺省或非法值回落默认——配置坏了不许拖垮拉取。"""
        environ = os.environ if env is None else env
        return cls(
            prometheus_url=environ.get(PROMETHEUS_URL_ENV, DEFAULT_PROMETHEUS_URL),
            window=timedelta(
                seconds=_positive_number(
                    environ.get(WINDOW_SECONDS_ENV), DEFAULT_WINDOW.total_seconds()
                )
            ),
            timeout=_positive_number(environ.get(TIMEOUT_SECONDS_ENV), DEFAULT_TIMEOUT_SECONDS),
        )


def _positive_number(raw: str | None, default: float) -> float:
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default
