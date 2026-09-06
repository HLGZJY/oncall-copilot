#!/usr/bin/env bash
# 剧本 11：版本协议不兼容——旧版本消息遇到新协议要求被静默跳过（业务语义层，必做）
#
# 机制：worker 消费每条任务消息时检查 redis 门禁 chaos:min_protocol；
#       注入把门禁抬到 v2，而 api-gw 发出的消息仍是 v1 → worker 正常消费
#       消息但判定"协议过旧"直接跳过（记 error 日志，不重新入队）。
#       这是典型语义层故障：无 5xx、无资源异常、队列正常排空、指标全绿，
#       唯一异常在业务面——任务永远 pending：
#       - demo_tasks_stale_pending（pending 超 60s 计数）> 5 持续 2m
#         → DemoTasksStuckPending（业务指标维度告警）
#       - Loki 可查 worker 的 "task protocol incompatible" 错误日志
#
# 环境变量：DURATION_SEC（秒，默认 360）
set -euo pipefail

DURATION_SEC="${DURATION_SEC:-360}"
REDIS_CONTAINER="${REDIS_CONTAINER:-oncall-demo-redis-1}"

# 1. 抬高协议门禁（消费端"要求"与生产端"能力"产生版本偏斜）
docker exec "$REDIS_CONTAINER" redis-cli SET chaos:min_protocol v2 >/dev/null

# 2. 业务探针：单线程每 2s 提交一条任务（payload 带剧本标记，cleanup 补偿用）
docker rm -f protocol-mismatch-probe >/dev/null 2>&1 || true
docker run -d --name protocol-mismatch-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import json, time, urllib.request
end = time.time() + ${DURATION_SEC}
while time.time() < end:
    try:
        req = urllib.request.Request(
            'http://api-gw:8000/tasks',
            data=json.dumps({'payload': 'protocol-mismatch-probe'}).encode(),
            headers={'Content-Type': 'application/json'},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
    time.sleep(2.0)
"

echo "[protocol-mismatch] 注入完成：门禁已抬到 v2，v1 消息将被消费后跳过"
echo "[protocol-mismatch] 观察：docker logs oncall-demo-worker-1 --tail 20 | grep incompatible"
echo "[protocol-mismatch]       curl 'http://127.0.0.1:9090/api/v1/query?query=demo_tasks_stale_pending'"
