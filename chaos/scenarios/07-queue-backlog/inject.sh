#!/usr/bin/env bash
# 剧本 07：队列堆积——生产速率超过 worker 消费速率（负载类 / 容器化压测探针）
#
# 机制：worker 消费能力 ≈ 9 task/s（concurrency=2 × 每任务 0.2s + DB 写入）。
#       探针 16 线程 × 每 1s 一条 ≈ 16 task/s，净堆积 ~7 task/s：
#       队列深度几秒内越过 20，持续堆积。
#       注入手段说明：沿 01/02 先例用单容器 python 探针（Locust/k6 的等价
#       轻量替身，无额外镜像依赖），inject_method 记 load-generator。
#
# 观测：demo_queue_depth > 20 持续 2m → DemoQueueDepthHigh
#       （队列深度读 broker 所在 redis db1，2026-09-06 修复后真实反映堆积）
#
# 环境变量：DURATION_SEC（秒，默认 300；需覆盖告警 for:2m）
set -euo pipefail

DURATION_SEC="${DURATION_SEC:-300}"

docker rm -f queue-backlog-probe >/dev/null 2>&1 || true
docker run -d --name queue-backlog-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
end = time.time() + ${DURATION_SEC}
def hit(_):
    while time.time() < end:
        try:
            req = urllib.request.Request(
                'http://api-gw:8000/tasks',
                data=json.dumps({'payload': 'queue-backlog-probe'}).encode(),
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass
        time.sleep(1.0)
with ThreadPoolExecutor(16) as ex:
    list(ex.map(hit, range(16)))
"

echo "[queue-backlog] 注入完成：16 线程 × 1/s ≈ 16 task/s（消费 ~9/s），净堆积 ~7/s"
echo "[queue-backlog] 观察：curl 'http://127.0.0.1:8000/health'"
echo "[queue-backlog]       curl 'http://127.0.0.1:9090/api/v1/query?query=ALERTS%7Balertname%3D%22DemoQueueDepthHigh%22%7D'"
