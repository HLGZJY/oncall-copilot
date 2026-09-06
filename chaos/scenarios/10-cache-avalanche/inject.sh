#!/usr/bin/env bash
# 剧本 10：缓存雪崩——任务读缓存被持续清空，读流量直打 DB（故障类）
#
# 机制：GET /tasks/{id} 走 redis 缓存（TTL 60s，db0）。注入拉起一个
#       "flusher" 容器每 10s FLUSHDB 一次——等价于缓存大规模同时失效/
#       被驱逐（真实雪崩的常见形态：批量 TTL 相同或 redis 内存淘汰风暴）。
#       每次清空后 24 线程探针对 40 个热点 id 的请求全部转 miss：
#       - demo_task_cache_operations_total{result="miss"} 速率骤增
#         → DemoCacheMissSpike
#       - miss 的读请求全部落到 DB → 连接池打满 → DemoDbPoolSaturated
#       FLUSHDB 只清 db0（缓存层），celery broker 在 db1 不受影响——
#       缓存故障不得连带击穿消息队列，这是剧本正交性的一部分。
#
# 环境变量：DURATION_SEC（秒，默认 300）、FLUSH_INTERVAL_SEC（默认 10）
set -euo pipefail

DURATION_SEC="${DURATION_SEC:-300}"
FLUSH_INTERVAL_SEC="${FLUSH_INTERVAL_SEC:-0.05}"

# 1. flusher：每 50ms 清一次缓存 db（默认）——热点键回填是秒级的，间隔过长会让雪崩窗口一闪而过
docker rm -f cache-avalanche-flusher >/dev/null 2>&1 || true
docker run -d --name cache-avalanche-flusher --network oncall-demo_default \
  redis:7-alpine sh -c "while true; do redis-cli -h redis -n 0 FLUSHDB >/dev/null; sleep ${FLUSH_INTERVAL_SEC}; done"

# 2. 业务探针：24 线程循环读 40 个热点任务 id（1~40 均已存在）
docker rm -f cache-avalanche-probe >/dev/null 2>&1 || true
docker run -d --name cache-avalanche-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import time, urllib.request
from concurrent.futures import ThreadPoolExecutor
end = time.time() + ${DURATION_SEC}
def hit(i):
    n = i % 40 + 1
    while time.time() < end:
        try:
            urllib.request.urlopen(f'http://api-gw:8000/tasks/{n}', timeout=35)
        except Exception:
            pass
        time.sleep(0.1)
with ThreadPoolExecutor(24) as ex:
    list(ex.map(hit, range(24)))
"

echo "[cache-avalanche] 注入完成：每 ${FLUSH_INTERVAL_SEC}s 清空缓存 db0，24 线程读热点 id"
echo "[cache-avalanche] 观察：curl 'http://127.0.0.1:9090/api/v1/query?query=sum(rate(demo_task_cache_operations_total%7Bresult%3D%22miss%22%7D%5B1m%5D))'"
