#!/usr/bin/env bash
# 黄金集单轮采集驱动（issue 05）：注入 → 等 firing → cleanup → 等恢复 → 落 dump 切片
# 用法: collect_run.sh <scenario-dir> <slug> <run-id>
#   例: collect_run.sh 04-downstream-timeout downstream-timeout r1
# 时间线数据源纪律：fired_at/resolved_at 一律取 alerts-dump.jsonl 实测值（Alertmanager
# webhook 的 startsAt/endsAt），不得编造；Prometheus API 仅用于判定 firing/恢复状态。
set -uo pipefail
cd "$(dirname "$0")/../../.."

SDIR="$1"; SLUG="$2"; RUN="$3"
DUMP=deploy/alerts-dump.jsonl
RAW=datasets/golden/_raw
PROM=http://127.0.0.1:9090
mkdir -p "$RAW"

# 期望告警名从 scenario.yaml 读取（注意：rules.yml 的 scenario 标签是静态来源标注，
# 不能用来判定"谁触发"——判定只看 alertname 是否 ∈ expected_alerts）
EXPECTED=$("C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -c "
import yaml,glob
for f in glob.glob('chaos/scenarios/*/scenario.yaml'):
    d=yaml.safe_load(open(f,encoding='utf-8'))
    if d['name']=='$SLUG': print(','.join(d['expected_alerts'])); break
")

py_firing_hits() {
  "C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -c "
import sys, json
names='$EXPECTED'.split(',')
res = json.load(sys.stdin)['data']['result']
hits = [r['metric']['alertname'] for r in res if r['metric'].get('alertstate')=='firing' and r['metric'].get('alertname') in names]
print(len(hits))
"
}

BASE=$(wc -l < "$DUMP")
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[run $RUN] started_at=$START  baseline_lines=$BASE"

bash "chaos/scenarios/$SDIR/inject.sh" >/dev/null
echo "[run $RUN] injected, waiting for firing (max 6min)..."

FIRED=0
for i in $(seq 1 36); do
  N=$(curl -s "$PROM/api/v1/query" --data-urlencode query=ALERTS | py_firing_hits)
  if [ "${N:-0}" -gt 0 ]; then FIRED=1; break; fi
  sleep 10
done
if [ "$FIRED" -ne 1 ]; then
  echo "[run $RUN] FAIL: 6min 内未见 firing——cleanup 后标记本轮失败，不编造数据"
  bash "chaos/scenarios/$SDIR/cleanup.sh" >/dev/null
  echo "{\"slug\":\"$SLUG\",\"run\":\"$RUN\",\"status\":\"no-firing\",\"started_at\":\"$START\"}" > "$RAW/$SLUG-$RUN.json"
  exit 1
fi
echo "[run $RUN] firing confirmed (n=$N), cleanup..."

bash "chaos/scenarios/$SDIR/cleanup.sh" >/dev/null
for i in $(seq 1 42); do
  N=$(curl -s "$PROM/api/v1/query" --data-urlencode query=ALERTS | py_firing_hits)
  if [ "${N:-0}" -eq 0 ]; then break; fi
  sleep 10
done
REC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[run $RUN] recovered_at=$REC (residual n=$N)"

# 等待 Alertmanager resolved webhook 全部落盘（group_interval 有延迟，最多再等 2min）
sleep 120
tail -n +$((BASE + 1)) "$DUMP" > "$RAW/$SLUG-$RUN.jsonl"
echo "{\"slug\":\"$SLUG\",\"run\":\"$RUN\",\"status\":\"ok\",\"started_at\":\"$START\",\"recovered_at\":\"$REC\"}" > "$RAW/$SLUG-$RUN.json"
echo "[run $RUN] dump slice saved: $RAW/$SLUG-$RUN.jsonl ($(wc -l < "$RAW/$SLUG-$RUN.jsonl") lines)"
