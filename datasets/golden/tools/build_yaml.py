"""从 _raw/<slug>-r<k>.jsonl 实测切片生成黄金集草稿 YAML（issue 05）。

纪律：
- fired_at/resolved_at 一律取 alerts-dump.jsonl 实测值（Alertmanager startsAt/endsAt），
  按告警指纹配对 firing→resolved；绝无编造时刻。
- 预标注（root_cause/remediation）取自 scenario.yaml（实测回填版）作为初稿；
  investigation_path 按剧本类别给初稿。**标注准确性核对权在人**——产出仅为草稿。
- 拆分：r1+r2 → datasets/golden/dev/<slug>.yaml，r3 → datasets/golden/holdout/<slug>.yaml。

用法: python build_yaml.py <slug>   # 读取 _raw/<slug>-r{1,2,3}.* 与对应 scenario.yaml
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
RAW = REPO / "datasets/golden/_raw"
ZERO = "0001-01-01T00:00:00Z"
EXPECTED_RUNS = 3  # 每剧本 ×3 轮（dev 2 + holdout 1）

INVESTIGATION_BY_CATEGORY = {
    "资源类": [
        "查 Prometheus ALERTS 与相关资源指标（CPU/内存）确认资源水位",
        "docker inspect/stats 查容器 cgroup 限制与实际用量",
        "排除部署变更后定位注入源/超限进程",
    ],
    "网络类": [
        "查 /tasks P95 与 5xx 率确认延迟形态（恒定抬升 vs 尾部尖刺）",
        "对比 api-gw 与 mysql 间网络指标/重传，区分延迟与丢包",
        "docker exec 进 api-gw 查 tc qdisc 确认 netem 注入",
    ],
    "业务类": [
        "查 /tasks P95、连接池占用与 5xx 率定位到 DB 路径",
        "查 MySQL processlist/data_locks 看持锁与等待会话",
        "确认锁类型（表锁/行锁/死锁环）与全局变量是否被改动",
    ],
    "负载类": [
        "查 QPS/提交速率与消费速率对比，确认净堆积方向",
        "查队列深度/连接池占用随时间曲线确认饱和点",
        "评估容量：扩容消费端或限流生产端",
    ],
    "故障类": [
        "查 up 指标与容器退出码/重启记录定位故障进程",
        "查业务指标（miss 率/滞留任务）确认受影响面",
        "重启/恢复组件后回查指标确认收敛",
    ],
    "业务语义层": [
        "确认资源指标全绿、无 5xx——排除基础设施层",
        "查业务指标（滞留任务）与 worker 日志找语义层异常",
        "比对生产端/消费端版本约定（协议门禁/消息版本）",
    ],
}


def parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def collect_timeline(slug: str, run: str, expected: set[str]) -> list[dict] | None:
    """配对 run 切片里的 firing/resolved，返回 alert_timeline 元素列表。

    只保留 expected_alerts 内的告警名——rules.yml 的 scenario 标签是静态来源标注
    （如 DemoApiGwHighLatency 固定标 cpu-spike），不能按标签归属剧本，必须按
    alertname ∈ expected_alerts 过滤。
    """
    f = RAW / f"{slug}-{run}.jsonl"
    if not f.exists():
        return None
    events: dict[str, dict] = {}  # fingerprint -> {alert_name, labels, fired_at, resolved_at}
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = None
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # dump 并发追加可能产生被截断的半行（baseline 按行数切片的固有瑕疵），跳过
            sys.stderr.write(f"[{slug}-{run}] 警告：切片内 1 行 JSON 解析失败，已跳过\n")
            continue
        for a in rec.get("alerts", []):
            fp = a["fingerprint"]
            labels = {k: str(v) for k, v in a.get("labels", {}).items()}
            if labels.get("alertname") not in expected:
                continue
            ev = events.setdefault(
                fp,
                {
                    "alert_name": labels["alertname"],
                    "labels": labels,
                    "fired_at": None,
                    "resolved_at": None,
                },
            )
            if a["status"] == "firing" and ev["fired_at"] is None:
                ev["fired_at"] = a["startsAt"]
            elif a["status"] == "resolved" and a["endsAt"] != ZERO:
                ev["resolved_at"] = a["endsAt"]
    timeline = []
    for ev in events.values():
        if ev["fired_at"] is None:
            continue
        timeline.append(
            {
                "alert_name": ev["alert_name"],
                "labels": ev["labels"],
                "fired_at": iso(parse_ts(ev["fired_at"])),
                "resolved_at": iso(parse_ts(ev["resolved_at"])) if ev["resolved_at"] else None,
            }
        )
    timeline.sort(key=lambda x: x["fired_at"])
    return timeline


def build_run(slug: str, run: str, started_hint: str | None, expected: set[str]) -> dict | None:
    timeline = collect_timeline(slug, run, expected)
    if not timeline:
        return None
    meta_f = RAW / f"{slug}-{run}.json"
    started = started_hint
    recovered = None
    if meta_f.exists():
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        started = meta.get("started_at") or started
        recovered = meta.get("recovered_at")
    if started is None:
        # 无 run 元数据时兜底：用最早 fired_at（started 本身早于 firing，取实测可得的最早时刻）
        started = timeline[0]["fired_at"]
    return {"started_at": started, "recovered_at": recovered, "alert_timeline": timeline}


def find_scenario_dir(slug: str) -> Path:
    """slug → chaos/scenarios/<NN>-<slug>/。

    目录名带序号前缀，按 scenario.yaml 的 name 字段匹配。
    """
    for f in (REPO / "chaos/scenarios").glob("*/scenario.yaml"):
        if yaml.safe_load(f.read_text(encoding="utf-8")).get("name") == slug:
            return f.parent
    msg = f"chaos/scenarios 下找不到 name={slug} 的剧本"
    raise SystemExit(msg)


def main() -> None:
    slug = sys.argv[1]
    spec_path = find_scenario_dir(slug) / "scenario.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    golden_common = {
        "scenario": slug,
        "root_cause": spec["expected_root_cause"],
        "investigation_path": INVESTIGATION_BY_CATEGORY[spec["category"]],
        "remediation": spec["expected_remediation"],
    }
    expected = set(spec["expected_alerts"])
    runs = {}
    for k in (1, 2, 3):
        r = build_run(slug, f"r{k}", None, expected)
        if r:
            runs[f"r{k}"] = r
    if len(runs) < EXPECTED_RUNS:
        msg = f"[{slug}] 警告：仅 {len(runs)} 轮（{sorted(runs)}），需补齐 dev2+holdout1"
        sys.stderr.write(msg + "\n")
        sys.exit(1)
    out = REPO / "datasets/golden"
    (out / "dev").mkdir(parents=True, exist_ok=True)
    (out / "holdout").mkdir(parents=True, exist_ok=True)
    for split, keys in (("dev", ["r1", "r2"]), ("holdout", ["r3"])):
        doc = {**golden_common, "runs": [runs[k] for k in keys]}
        p = out / split / f"{slug}.yaml"
        header = (
            "# 黄金集草稿（issue 05）——预标注初稿待人工核对，核对记录写入 issue Comments\n"
            f"# 场景: {spec['fault_type']}（{spec['category']}）\n"
            f"# 数据源: deploy/alerts-dump.jsonl 实测切片（fired_at/resolved_at 未编造）\n"
            f"# 标注状态: DRAFT（Agent 初稿）→ 待人工抽核 → 双签回填\n\n"
        )
        body = header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        p.write_text(body, encoding="utf-8")
        timeline_sizes = [len(r["alert_timeline"]) for r in doc["runs"]]
        sys.stdout.write(
            f"[{slug}] wrote {p} ({len(doc['runs'])} runs, timeline {timeline_sizes})\n"
        )


if __name__ == "__main__":
    main()
