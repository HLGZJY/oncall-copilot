#!/usr/bin/env bash
# 剧本 07 清理：停探针，等 worker 把积压自然消化完
set -euo pipefail

docker rm -f queue-backlog-probe >/dev/null 2>&1 || true

DEPTH="$(curl -s http://127.0.0.1:8000/health | grep -o '"queue_depth": [0-9-]*' | grep -o '[0-9-]*')"
echo "[queue-backlog] 清理完成：探针已停，当前积压 ${DEPTH} 条"
echo "[queue-backlog] 消费速率约 9/s，预计 ${DEPTH} 秒内排空（DURATION 越长排空越久）"
