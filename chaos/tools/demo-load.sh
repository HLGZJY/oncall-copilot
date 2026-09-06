#!/usr/bin/env bash
# demo 业务负载探针（issue 06 / make demo-load）
#
# 机制：8 进程 × 8 线程 keep-alive POST /tasks，制造真实业务流量。
#   - 必须 keep-alive：短连接下客户端 TCP 开销盖过服务端（issue 04 实测，
#     池占用只到 3~4），且 Windows/git bash 宿主机大量派生进程不可靠 → 容器化探针；
#   - 默认强度（64 连接）为"演示/预热负载"，不触发告警；
#     要复现队列堆积告警请用剧本 07（make inject SCENARIO=07-queue-backlog）。
#
# 环境变量：DURATION_SEC（秒，默认 60）
set -euo pipefail

DURATION_SEC="${DURATION_SEC:-60}"

docker rm -f demo-load-probe >/dev/null 2>&1 || true
docker run -d --name demo-load-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import http.client, json, time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Pool

end = time.time() + ${DURATION_SEC}

def hit(_):
    conn = http.client.HTTPConnection('api-gw', 8000, timeout=35)
    while time.time() < end:
        try:
            conn.request(
                'POST', '/tasks',
                json.dumps({'payload': 'demo-load'}),
                {'Content-Type': 'application/json'},
            )
            conn.getresponse().read()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            conn = http.client.HTTPConnection('api-gw', 8000, timeout=35)

def run_workers(_):
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(hit, range(8)))

if __name__ == '__main__':
    with Pool(8) as p:
        p.map(run_workers, range(8))
"

echo "[demo-load] 负载探针已启动：64 keep-alive 连接 POST /tasks，持续 ${DURATION_SEC}s"
echo "[demo-load] 观察：curl 'http://127.0.0.1:9090/api/v1/query?query=demo_requests_total'"
