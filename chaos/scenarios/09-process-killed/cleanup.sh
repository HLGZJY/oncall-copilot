#!/usr/bin/env bash
# 剧本 09 清理：先停 Pumba（防止 worker 被反复杀）再拉起 worker
set -euo pipefail

WORKER_CONTAINER="${WORKER_CONTAINER:-oncall-demo-worker-1}"

# 1. 先停 Pumba——它处于 kill 值守循环，必须先移除再拉起 worker
if docker ps -a --format '{{.Names}}' | grep -qx pumba-process-killed; then
  docker stop -t 5 pumba-process-killed >/dev/null
  docker rm pumba-process-killed >/dev/null
fi
docker rm -f process-killed-probe >/dev/null 2>&1 || true

# 2. 拉起被杀的 worker
STATE="$(docker inspect -f '{{.State.Status}}' "$WORKER_CONTAINER")"
if [[ "$STATE" != "running" ]]; then
  docker start "$WORKER_CONTAINER" >/dev/null
  echo "[process-killed] worker 已重新启动，开始消化积压（约 9/s）"
fi

echo "[process-killed] 清理完成：Pumba 已移除，worker 已恢复运行"
