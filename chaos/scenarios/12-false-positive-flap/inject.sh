#!/usr/bin/env bash
# 剧本 12：规则阈值配置漂移型误报（D-21 / issue 06）——不注入任何业务故障
#
# 机制：把误报专属规则 DemoTasksLatencyFlap 的 /tasks P95 阈值从默认 0.12s 临时
#       下调到 0.01s（正常水位实测稳定在 ~25ms，见 issue 06 Comments 基线记录），
#       正常流量即持续越限 1m → Prometheus 真实评估链路 firing。告警是真实产生的，
#       只是规则配置错了——配置漂移型误报的标准形态（非伪造 webhook payload）。
#
# reload 方式（issue 06 实测）：Prometheus 容器未开 --web.enable-lifecycle，
# /-/reload 不可用；SIGHUP（docker kill -s HUP）触发配置重载，实测生效。
#
# 幂等：重复执行不重复备份、不重复改阈值；流量探针 rm -f 后重建。
# 环境变量：DURATION_SEC（秒，默认 1200；流量探针存活时长，需覆盖等 firing + 采集窗口）
set -euo pipefail

DURATION_SEC="${DURATION_SEC:-1200}"
RULES_FILE="deploy/prometheus/rules.yml"
BAK="/tmp/oncall-12-false-positive-flap-rules.yml.bak"
PROM_CONTAINER="${PROM_CONTAINER:-oncall-demo-prometheus-1}"

cd "$(dirname "$0")/../../.."  # 回仓库根（collect_run.sh 同款约定，双保险）

# 1. 备份规则文件（只在首次注入时备份，保证还原的是注入前状态）
if [ ! -f "$BAK" ]; then
  cp "$RULES_FILE" "$BAK"
fi

# 2. 阈值漂移：0.12 → 0.01（已是漂移态则跳过，幂等）
if ! grep -q ') > 0.01' "$RULES_FILE"; then
  sed -i 's/) > 0.12/) > 0.01/' "$RULES_FILE"
fi

# 3. 校验 + reload（promtool 先验；SIGHUP 重载，坏配置会被 Prometheus 拒载且旧规则继续生效）
docker exec "$PROM_CONTAINER" promtool check rules /etc/prometheus/rules.yml
docker kill -s HUP "$PROM_CONTAINER"

# 4. 正常业务流量探针：2 线程 × 1s 间隔 POST /tasks（≈2/s，远低于 worker 消费能力
#    ~10/s，实测健康水位：P95 ~25ms、stale_pending=0、无任何告警——误报只能由
#    阈值漂移触发，保证"无真实业务故障"的剧本口径）
docker rm -f false-positive-flap-probe >/dev/null 2>&1 || true
docker run -d --name false-positive-flap-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import http.client, json, time
end = time.time() + ${DURATION_SEC}
def hit(_):
    conn = http.client.HTTPConnection('api-gw', 8000, timeout=10)
    while time.time() < end:
        try:
            conn.request('POST', '/tasks', json.dumps({'payload': 'false-positive-flap'}), {'Content-Type': 'application/json'})
            conn.getresponse().read()
        except Exception:
            try: conn.close()
            except Exception: pass
            conn = http.client.HTTPConnection('api-gw', 8000, timeout=10)
        time.sleep(1.0)
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(2) as ex:
    list(ex.map(hit, range(2)))
"

echo "[false-positive-flap] 注入完成：DemoTasksLatencyFlap 阈值 0.12s→0.01s（配置漂移），业务零故障"
echo "[false-positive-flap] 观察：curl 'http://127.0.0.1:9090/api/v1/query?query=ALERTS{alertname=\"DemoTasksLatencyFlap\"}'"
