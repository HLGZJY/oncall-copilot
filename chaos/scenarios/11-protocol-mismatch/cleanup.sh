#!/usr/bin/env bash
# 剧本 11 清理：恢复协议门禁 + 人工补偿滞留任务（语义层故障的处置形态）
set -euo pipefail

REDIS_CONTAINER="${REDIS_CONTAINER:-oncall-demo-redis-1}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-oncall-demo-mysql-1}"

docker rm -f protocol-mismatch-probe >/dev/null 2>&1 || true

# 1. 撤门禁：后续新任务恢复正常处理
docker exec "$REDIS_CONTAINER" redis-cli DEL chaos:min_protocol >/dev/null

# 2. 补偿：被跳过的消息不会重新入队（真实版本偏斜事故的数据丢失形态），
#    处置 = 将滞留任务落终态。消息重放能力属 M1 ingest 范畴，此处以终态解除业务滞留。
STUCK="$(docker exec "$MYSQL_CONTAINER" mysql -uroot -poncall demo -N \
  -e "SELECT COUNT(*) FROM tasks WHERE payload='protocol-mismatch-probe' AND status='pending'")"
docker exec "$MYSQL_CONTAINER" mysql -uroot -poncall demo \
  -e "UPDATE tasks SET status='failed' WHERE payload='protocol-mismatch-probe' AND status='pending'"
echo "[protocol-mismatch] 清理完成：门禁已撤，$STUCK 条滞留任务已补偿为 failed 终态"
