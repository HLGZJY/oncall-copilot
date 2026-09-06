#!/usr/bin/env bash
# 剧本 06：数据库死锁——两个事务互相持有对方需要的锁且死锁检测被关闭（业务类）
#
# 机制（真实锁环 + 检测关闭 = 持续死锁）：
#   会话 A：BEGIN; 锁 tasks 第 1 行 → 5s 后申请"最后一行 + supremum 区间锁"
#   会话 B：BEGIN; 锁"最后一行 + supremum 区间锁" → 5s 后申请第 1 行
#   两者构成锁环。InnoDB 默认 1s 内检测死锁并回滚受害者，因此注入先
#   SET GLOBAL innodb_deadlock_detect=OFF——这是"死锁持续存在"的前提，
#   也是本剧本模拟的根因之一（生产上常见于大事务 + 检测超时被调大）。
#   supremum next-key 锁一旦被持有，所有 INSERT 进 tasks 都被阻塞：
#   api-gw 的 POST /tasks 全部挂起 → 连接池打满 → 30s 超时产生 5xx。
#
# 观测：12 线程探针并发 POST /tasks
#       - DemoTasksHighLatency（P95 直方图顶格 + 5xx 支路兜底）
#       - DemoDbPoolSaturated（10 连接全部阻塞在锁上）
#       - DemoHighErrorRate（连接池超时的 5xx 流量）
#
# 环境变量：DURATION（秒，默认 300）
set -euo pipefail

DURATION="${DURATION:-300}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-oncall-demo-mysql-1}"
MARKER="deadlock-chaos"

# 1. 关闭死锁检测（全局）——锁环因此不会被 InnoDB 自动解开
docker exec "$MYSQL_CONTAINER" mysql -uroot -poncall \
  -e "SET GLOBAL innodb_deadlock_detect=OFF;"

# 2. 会话 A：先锁第 1 行，5s 后申请末行+supremum（被 B 持有 → 挂起）
#    所有语句都带 marker：会话可能停在任意一条上等待，cleanup 靠它定位 KILL
docker exec -d "$MYSQL_CONTAINER" mysql -uroot -poncall demo \
  -e "SET SESSION innodb_lock_wait_timeout=86400; BEGIN; SELECT /*$MARKER*/ id FROM tasks WHERE id=1 FOR UPDATE; SELECT SLEEP(5); SELECT /*$MARKER*/ id FROM tasks ORDER BY id DESC LIMIT 1 FOR UPDATE; SELECT /*$MARKER*/ SLEEP(${DURATION});"

# 3. 会话 B：先锁末行+supremum，5s 后申请第 1 行（被 A 持有 → 挂起，锁环闭合）
docker exec -d "$MYSQL_CONTAINER" mysql -uroot -poncall demo \
  -e "SET SESSION innodb_lock_wait_timeout=86400; BEGIN; SELECT /*$MARKER*/ id FROM tasks ORDER BY id DESC LIMIT 1 FOR UPDATE; SELECT SLEEP(5); SELECT /*$MARKER*/ id FROM tasks WHERE id=1 FOR UPDATE; SELECT /*$MARKER*/ SLEEP(${DURATION});"

# 4. 业务探针：12 线程并发 POST /tasks（INSERT 被 supremum 锁阻塞）
docker rm -f db-deadlock-probe >/dev/null 2>&1 || true
docker run -d --name db-deadlock-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import json, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
end = time.time() + ${DURATION}
def hit(_):
    while time.time() < end:
        try:
            req = urllib.request.Request(
                'http://api-gw:8000/tasks',
                data=json.dumps({'payload': 'db-deadlock-probe'}).encode(),
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=40)
        except Exception:
            pass
        time.sleep(0.5)
with ThreadPoolExecutor(12) as ex:
    list(ex.map(hit, range(12)))
"

echo "[db-deadlock] 注入完成：锁环已闭合（死锁检测已关闭），12 线程探针已启动"
echo "[db-deadlock] 观察：docker exec $MYSQL_CONTAINER mysql -uroot -poncall -e 'SELECT * FROM performance_schema.data_locks WHERE LOCK_STATUS=\"WAITING\"\\G' | head -30"
