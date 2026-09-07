"""prompt 组装快照测试（issue 03 / G3 ③）。

定案口径：系统角色（SRE 分诊员）+ 三态定义（含 D-07 风险语义）+ few-shot 样本
+ 事件卡片 JSON（D-17 形状，上下文时间锚 last_fired_at）+ 输出协议。
快照 = 结构性断言（稳定 prompt 结构齐全），不做逐字符 golden 文件比对——
结构稳定即可保证 prompt 演进可审计。
"""

from __future__ import annotations

from typing import Any

from oncall.classify.llm.fewshot import FewShotSample
from oncall.classify.llm.prompt import build_prompt


def make_card() -> dict[str, Any]:
    """最小 D-17 形状卡片：{alert, context, generated_at}，时间锚 last_fired_at。"""
    return {
        "alert": {
            "id": 1,
            "fingerprint": "fp-demo",
            "labels": {"alertname": "DemoApiGwHighLatency", "job": "api-gw", "severity": "warning"},
            "status": "deduped",
            "fired_at": "2026-09-07T06:28:21Z",
            "last_fired_at": "2026-09-07T06:31:21Z",
            "dedup_count": 3,
        },
        "context": {"metrics": {"p95": "0.95s"}, "topology": [], "changes": []},
        "generated_at": "2026-09-07T06:31:30Z",
    }


def make_samples() -> list[FewShotSample]:
    return [
        FewShotSample(
            scenario="cache-avalanche",
            alert_card={"alert": {"labels": {"alertname": "RedisHitRateLow"}, "fired_at": "t1"}},
            verdict="incident",
            reason="缓存命中率骤降导致 DB 压力升高",
        ),
        FewShotSample(
            scenario="false-positive-flap",
            alert_card={"alert": {"labels": {"alertname": "DemoLatency"}, "fired_at": "t2"}},
            verdict="false_positive",
            reason="阈值配置漂移导致正常水位误触发",
        ),
    ]


def test_system_contains_role_three_states_and_protocol() -> None:
    bundle = build_prompt(make_card(), make_samples())

    assert "SRE 分诊员" in bundle.system
    # 三态定义齐全，且 risk 含 D-07「不丢弃」语义
    for state in ("false_positive", "risk", "incident"):
        assert state in bundle.system
    assert "不丢弃" in bundle.system
    # 输出协议：LLM 不直出 risk，字段契约 + JSON mode 提示（R2）
    assert (
        "verdict" in bundle.system and "confidence" in bundle.system and "reason" in bundle.system
    )
    assert "risk" not in _verdict_allowed_values(bundle.system)
    assert "JSON" in bundle.system


def _verdict_allowed_values(system: str) -> list[str]:
    """从输出协议行提取 verdict 允许值集合（粗提取，只服务本断言）。"""
    for line in system.splitlines():
        if "verdict" in line and "只允许" in line:
            return [tok.strip('" ，。') for tok in line.replace("verdict", "").split()]
    return []


def test_user_contains_fewshot_card_and_protocol_echo() -> None:
    bundle = build_prompt(make_card(), make_samples())

    # few-shot 样本按场景名进入 user 段，且带标注 verdict
    assert "cache-avalanche" in bundle.user
    assert "false-positive-flap" in bundle.user
    assert '"verdict": "incident"' in bundle.user
    assert '"verdict": "false_positive"' in bundle.user
    # 待分类卡片 JSON 完整进入，含 D-17 上下文时间锚 last_fired_at
    assert "last_fired_at" in bundle.user
    assert "DemoApiGwHighLatency" in bundle.user


def test_prompt_is_deterministic() -> None:
    """同一输入两次组装结果逐字一致（快照稳定的前提）。"""
    card = make_card()
    samples = make_samples()

    first = build_prompt(card, samples)
    second = build_prompt(card, samples)

    assert first.system == second.system
    assert first.user == second.user


def test_empty_fewshot_pool_still_produces_valid_prompt() -> None:
    """当前 dev 集 false_positive 类为空（06 未落地）→ 样本池可只有 incident 类。"""
    bundle = build_prompt(make_card(), make_samples()[:1])

    assert "false-positive-flap" not in bundle.user
    assert "cache-avalanche" in bundle.user
    assert "SRE 分诊员" in bundle.system
