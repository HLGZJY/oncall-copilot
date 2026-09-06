#!/usr/bin/env bash
# 剧本 02 清理：KILL 持锁会话释放 tasks 表锁、停掉业务探针
set -euo pipefail

MYSQL_CONTAINER="${MYSQL_CONTAINER:-oncall-demo-mysql-1}"

# 1. 停探针容器（若还在跑）
docker rm -f slow-sql-probe >/dev/null 2>&1 || true
rm -f /tmp/oncall-demo-slow-sql.probe.pid

# 2. KILL 持有 SLEEP 的会话（即注入会话），锁随之释放
KILL_SQL="SELECT id FROM information_schema.processlist WHERE command='Query' AND info LIKE '%SLEEP(%'"
PIDS="$(docker exec "$MYSQL_CONTAINER" mysql -uroot -poncall -N -e "$KILL_SQL" || true)"
KILLED=0
for pid in $PIDS; do
  docker exec "$MYSQL_CONTAINER" mysql -uroot -poncall -e "KILL $pid" || true
  KILLED=$((KILLED + 1))
done

echo "[slow-sql] 清理完成：KILL 掉 $KILLED 个持锁会话，tasks 表锁已释放"
echo "[slow-sql] 阻塞中的请求将在连接池超时（≤40s）内排空，延迟随后恢复"
