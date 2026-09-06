#!/usr/bin/env bash
# 剧本 10 清理：停 flusher 与探针；缓存随请求自然回温，miss 率回落
set -euo pipefail

docker rm -f cache-avalanche-flusher >/dev/null 2>&1 || true
docker rm -f cache-avalanche-probe >/dev/null 2>&1 || true
echo "[cache-avalanche] 清理完成：flusher 已停，缓存随流量自然回温"
