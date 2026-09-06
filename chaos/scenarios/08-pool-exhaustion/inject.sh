#!/usr/bin/env bash
# 剧本 08：连接打满——并发读请求把 SQLAlchemy 连接池占满（负载类 / 压测探针）
#
# 机制：探针 192 个 keep-alive 长连接持续 GET 不存在的任务 id（1e6~1e9 随机）：
#       缓存必未命中 → 每次 GET 都要占用一个池连接做 pre_ping + PK 查询。
#       必须用 keep-alive：短连接下客户端 TCP 开销盖过服务端，服务端线程
#       （uvicorn 线程池 40 槽）打不满，DB 并发需求永远到不了池上限——
#       实测短连接时 pool_used 仅 3~4。长连接让服务端成为瓶颈，40 个活动
#       请求 × ~50% DB 时间占比 ≈ 20 并发需求 > 池上限 10 → pool_used 钉在 10。
#       选 404 读路径做负载源是为了与剧本 07 正交：不产生 celery 队列堆积。
#       级联表现（如实登记）：404 流量天然 100% cache miss → DemoCacheMissSpike
#       同步触发；线程池饱和连带非 DB 路径延迟抬升 → DemoApiGwHighLatency 同步触发。
#
# 观测：demo_db_pool_used >= demo_db_pool_size 持续 30s → DemoDbPoolSaturated
#
# 环境变量：DURATION_SEC（秒，默认 300）
set -euo pipefail

DURATION_SEC="${DURATION_SEC:-300}"

docker rm -f pool-exhaustion-probe >/dev/null 2>&1 || true
docker run -d --name pool-exhaustion-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import http.client, random, time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Pool

end = time.time() + ${DURATION_SEC}

def hit(_):
    conn = http.client.HTTPConnection('api-gw', 8000, timeout=35)
    while time.time() < end:
        try:
            conn.request('GET', f'/tasks/{random.randint(1000000, 999999999)}')
            conn.getresponse().read()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            conn = http.client.HTTPConnection('api-gw', 8000, timeout=35)

def run_workers(_):
    with ThreadPoolExecutor(24) as ex:
        list(ex.map(hit, range(24)))

if __name__ == '__main__':
    # 16 进程 × 24 线程：单进程多线程会受 GIL 串行化，负载锯齿导致池饱和抖动
    with Pool(16) as p:
        p.map(run_workers, range(16))
"

echo "[pool-exhaustion] 注入完成：192 keep-alive 连接 404 读路径，并发需求 > 池上限 10"
echo "[pool-exhaustion] 观察：curl 'http://127.0.0.1:9090/api/v1/query?query=demo_db_pool_used'"
