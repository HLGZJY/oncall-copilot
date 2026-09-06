#!/usr/bin/env bash
# 剧本 06 清理：KILL 两个死锁会话解除锁环、恢复死锁检测、停探针
set -euo pipefail

MYSQL_CONTAINER="${MYSQL_CONTAINER:-oncall-demo-mysql-1}"

docker rm -f db-deadlock-probe >/dev/null 2>&1 || true

# 1. 两轮 KILL：第一轮杀掉一个会话会让另一个从锁等待中醒来并继续持有锁，
#    第二轮确保全部终止（其事务回滚，锁全部释放）
for round in 1 2; do
  PIDS="$(docker exec "$MYSQL_CONTAINER" mysql -uroot -poncall -N \
    -e "SELECT id FROM information_schema.processlist WHERE info LIKE '%deadlock-chaos%'" || true)"
  [[ -z "$PIDS" ]] && break
  for pid in $PIDS; do
    docker exec "$MYSQL_CONTAINER" mysql -uroot -poncall -e "KILL $pid" || true
  done
  sleep 1
done

# 2. 恢复死锁检测（全局默认值）
docker exec "$MYSQL_CONTAINER" mysql -uroot -poncall \
  -e "SET GLOBAL innodb_deadlock_detect=ON;"

echo "[db-deadlock] 清理完成：死锁会话已终止，innodb_deadlock_detect 已恢复 ON"
echo "[db-deadlock] 阻塞请求将在连接池超时（≤40s）内排空"
