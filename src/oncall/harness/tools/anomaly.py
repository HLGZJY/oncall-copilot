"""detect_anomaly 工具（G3 / D-24）：纯统计 v1，零新依赖。

- stdlib `statistics` 实现，三通道并集：基线窗口 z-score + 分位数/IQR fences
  + 环比突变（D-24 定案口径）
- 基线窗口 = 序列前半段（≥ MIN_POINTS 点）；基线 σ 退化（全等序列）时回退
  全序列 σ——否则恒定基线上的尖峰会因 σ=0 被跳过
- IsolationForest/sklearn 不引入（M7 前按需评审，D-24）；纯函数无 Fetcher
- 四态接口完整：ok（检出异常点）/ empty（无异常）/ error（序列过短无法计算）
  / unavailable 不适用但保持接口一致（Registry 四态封装不特判）
- meta.method 标 `stat-v1-*` 供证据链叙事（可解释性，D-24）
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from oncall.harness.tools.schemas import DetectAnomalyInput, ToolResult, ToolStatus

MIN_POINTS = 3  # 少于 3 点无法建立统计基线
Z_THRESHOLD = 3.0  # z-score 阈值（|z| ≥ 3 判异常）
IQR_FACTOR = 1.5  # Tukey fences 系数
JUMP_STDEV_FACTOR = 3.0  # 环比突变：相邻差 ≥ 3σ（基线 σ）
METHOD = "stat-v1-zscore-iqr-jump"

__all__ = ["detect_anomaly_handler"]


def _error(reason: str) -> ToolResult:
    return ToolResult(
        tool="detect_anomaly", status=ToolStatus.ERROR, data=None, meta={"reason": reason}
    )


@dataclass(frozen=True)
class _Baseline:
    """统计基线：z-score 用 mean/σ；IQR fences 用 q1/q3；jump 用 σ。"""

    mean: float
    stdev: float
    q1: float
    q3: float

    @property
    def iqr(self) -> float:
        return self.q3 - self.q1


def detect_anomaly_handler(args: DetectAnomalyInput, *, timeout_seconds: float) -> ToolResult:
    """纯统计异常检测：三通道并集，按索引升序输出（timeout_seconds 对纯函数不适用）。"""
    values = args.values
    if len(values) < MIN_POINTS:
        return _error(f"序列过短（{len(values)} 点 < {MIN_POINTS}），无法计算统计基线")
    baseline = values[: max(MIN_POINTS, len(values) // 2)]
    mean = statistics.fmean(baseline)
    stdev = statistics.stdev(baseline) if len(baseline) > 1 else 0.0
    if stdev == 0.0:
        stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    q1, q3 = statistics.quantiles(values, n=4)[0], statistics.quantiles(values, n=4)[2]
    stats = _Baseline(mean=mean, stdev=stdev, q1=q1, q3=q3)
    anomalies: list[dict[str, object]] = []
    for index, value in enumerate(values):
        methods = _flag(value, index, values, stats)
        if methods:
            anomalies.append(
                {
                    "index": index,
                    "timestamp": args.timestamps[index].isoformat(),
                    "value": value,
                    "methods": methods,
                }
            )
    if not anomalies:
        return ToolResult(
            tool="detect_anomaly",
            status=ToolStatus.EMPTY,
            data=None,
            meta={"method": METHOD, "points": len(values)},
        )
    return ToolResult(
        tool="detect_anomaly",
        status=ToolStatus.OK,
        data={"anomalies": anomalies},
        meta={"method": METHOD, "points": len(values)},
    )


def _flag(
    value: float,
    index: int,
    values: list[float],
    stats: _Baseline,
) -> list[str]:
    """三通道判定：zscore（基线）/ iqr（Tukey fences）/ jump（环比，基线 σ）。"""
    methods: list[str] = []
    if stats.stdev > 0 and abs(value - stats.mean) / stats.stdev >= Z_THRESHOLD:
        methods.append("zscore")
    if stats.iqr > 0 and (
        value < stats.q1 - IQR_FACTOR * stats.iqr or value > stats.q3 + IQR_FACTOR * stats.iqr
    ):
        methods.append("iqr")
    jump = index > 0 and abs(value - values[index - 1]) >= JUMP_STDEV_FACTOR * stats.stdev
    if stats.stdev > 0 and jump:
        methods.append("jump")
    return methods
