#!/usr/bin/env bash
# 剧本 02：慢 SQL——tasks 表长持写锁（业务类 / 自定义脚本）
#
# 机制：注入会话 LOCK TABLES tasks WRITE + SELECT SLEEP，锁持有期间
#       api-gw 对 tasks 的所有读写都阻塞在表锁上：
#         - 12 线程探针并发 POST /tasks：前 10 个占满 SQLAlchemy 连接池（5+5）
#           阻塞在锁上，后续请求 30s 连接池超时产生 500（已完成的高延迟样本）
#         - /tasks P95 直冲直方图顶格 → DemoTasksHighLatency
#         - demo_db_pool_used 达到 size → DemoDbPoolSaturated
#       /health 只查 SELECT 1 不碰 tasks，保持快速——与 CPU 剧本正交。
#       探针必须并发：串行单请求只会占 1 个连接，池饱和与告警样本都出不来。
#
# 环境变量：DURATION（秒，默认 300，需覆盖告警 for:1m + 30s 连接池超时的样本延迟）
set -euo pipefail

DURATION="${DURATION:-300}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-oncall-demo-mysql-1}"

# 1. 持锁会话（分离执行，后台 SLEEP 期间锁一直被持有）
docker exec -d "$MYSQL_CONTAINER" mysql -uroot -poncall demo \
  -e "SET SESSION lock_wait_timeout=86400; LOCK TABLES tasks WRITE; SELECT SLEEP(${DURATION}); UNLOCK TABLES;"

# 2. 业务探针：单容器 12 线程并发 POST /tasks（不用宿主机循环 curl：Windows 不可靠）
docker rm -f slow-sql-probe >/dev/null 2>&1 || true
docker run -d --name slow-sql-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import json, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
end = time.time() + ${DURATION}
def hit(_):
    while time.time() < end:
        try:
            req = urllib.request.Request(
                'http://api-gw:8000/tasks',
                data=json.dumps({'payload': 'slow-sql-probe'}).encode(),
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=40)
        except Exception:
            pass
        time.sleep(0.5)
with ThreadPoolExecutor(12) as ex:
    list(ex.map(hit, range(12)))
"

echo "[slow-sql] 注入完成：tasks 写锁持有 ${DURATION}s，12 线程探针已启动"
echo "[slow-sql] 观察：curl 'http://127.0.0.1:9090/api/v1/query?query=demo_db_pool_used'"
