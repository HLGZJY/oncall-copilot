"""T3 验收测试（纯函数接缝）：告警指纹 canonical sha256（issue 03 / G1 定案）。

契约来源：docs/design/m1-alert-ingestion-design.md G1 + 评审依据 R2
（prometheus/common `SignatureWithoutLabels`：稳定 label 子集 + 排序 + 0xFF 分隔）、
CONTEXT.md「告警指纹 = 由规则+实例+时间窗算出的稳定哈希」。

覆盖：
- 稳定性：annotations / 易变 label / 字段顺序变化 → 指纹不变（数值抖动不误分）
- 区分度：alertname / job / instance 任一变化 → 指纹变
- 0xFF 分隔防拼接碰撞（`ab`+`c` 与 `a`+`bc`）
- 时间窗桶：同窗同指纹、跨窗新指纹、dedup_window 可配
"""

from datetime import UTC, datetime, timedelta

from oncall.ingest.fingerprint import (
    DEFAULT_DEDUP_WINDOW,
    candidate_fingerprints,
    canonical_fingerprint,
)

T0 = datetime(2026, 9, 6, 6, 0, 0, tzinfo=UTC)
CANONICAL = {"alertname": "DemoApiGwHighLatency", "job": "api-gw", "instance": "api-gw-1"}


def _fp(
    labels: dict[str, str],
    fired_at: datetime = T0,
    window: timedelta = DEFAULT_DEDUP_WINDOW,
) -> str:
    return canonical_fingerprint(labels, fired_at, window)


class TestStability:
    def test_volatile_labels_do_not_change_fingerprint(self):
        """severity/scenario 等易变 label 不入哈希（G1：只吃稳定子集）。"""
        volatile = {
            **CANONICAL,
            "severity": "warning",
            "scenario": "cpu-spike",
            "pod": "api-gw-1-abc",
        }
        assert _fp(volatile) == _fp(CANONICAL)

    def test_annotations_and_values_excluded(self):
        """annotations/数值不入哈希——指纹只吃 labels 子集，构造上就不可能受影响。"""
        assert _fp(CANONICAL) == _fp(dict(CANONICAL))

    def test_label_insertion_order_irrelevant(self):
        """哈希输入按 label 名排序，dict 顺序无关。"""
        forward = {"alertname": "A", "job": "j", "instance": "i"}
        backward = {"instance": "i", "job": "j", "alertname": "A"}
        assert _fp(forward) == _fp(backward)

    def test_naive_datetime_treated_as_utc(self):
        """缺时区信息的时间戳按 UTC 处理（SQLite 取回无 tzinfo，比较口径统一）。"""
        assert _fp(CANONICAL, T0) == _fp(CANONICAL, T0.replace(tzinfo=None))

    def test_is_64_hex_chars(self):
        digest = _fp(CANONICAL)
        assert len(digest) == 64
        int(digest, 16)


class TestDiscrimination:
    def test_instance_change_changes_fingerprint(self):
        assert _fp(CANONICAL) != _fp({**CANONICAL, "instance": "api-gw-2"})

    def test_job_change_changes_fingerprint(self):
        assert _fp(CANONICAL) != _fp({**CANONICAL, "job": "worker"})

    def test_alertname_change_changes_fingerprint(self):
        assert _fp(CANONICAL) != _fp({**CANONICAL, "alertname": "DemoSqlSlow"})

    def test_separator_prevents_concatenation_collision(self):
        """0xFF 分隔（R2 先例）：`a`+`bc` 与 `ab`+`c` 不可碰撞。"""
        left = {"alertname": "a", "job": "bc"}
        right = {"alertname": "ab", "job": "c"}
        assert _fp(left) != _fp(right)

    def test_missing_canonical_labels_still_deterministic(self):
        """存在者参与（G1）：只带 alertname 也能算，且稳定。"""
        only_name = {"alertname": "DemoApiGwHighLatency"}
        assert _fp(only_name) == _fp(dict(only_name))
        assert _fp(only_name) != _fp({**only_name, "instance": "api-gw-1"})


class TestTimeWindow:
    def test_default_window_is_10_minutes(self):
        assert DEFAULT_DEDUP_WINDOW == timedelta(minutes=10)

    def test_same_window_same_fingerprint(self):
        assert _fp(CANONICAL, T0) == _fp(CANONICAL, T0 + timedelta(minutes=5))

    def test_beyond_window_new_fingerprint(self):
        """超窗 → 新时间窗桶 → 新指纹 → 新行（唯一约束下合并与新行可并存）。"""
        assert _fp(CANONICAL, T0) != _fp(CANONICAL, T0 + timedelta(minutes=11))

    def test_window_is_configurable(self):
        """dedup_window 可调：1 分钟窗下 30s 合并、90s 分行。"""
        short = timedelta(minutes=1)
        assert _fp(CANONICAL, T0, short) == _fp(CANONICAL, T0 + timedelta(seconds=30), short)
        assert _fp(CANONICAL, T0, short) != _fp(CANONICAL, T0 + timedelta(seconds=90), short)

    def test_candidate_fingerprints_cover_current_and_previous_bucket(self):
        """跨桶合并需要查当前桶 + 前一桶（滑窗语义，桶宽 = 窗口长度）。"""
        candidates = candidate_fingerprints(CANONICAL, T0)
        assert len(candidates) == 2
        assert candidates[0] == _fp(CANONICAL, T0)
        # 前一桶 = 当前桶起点前一窗口的时间点
        assert candidates[1] == _fp(CANONICAL, T0 - DEFAULT_DEDUP_WINDOW)
