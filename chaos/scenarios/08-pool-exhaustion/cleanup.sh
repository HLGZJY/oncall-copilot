#!/usr/bin/env bash
# 剧本 08 清理：停探针，连接即还池（无持久状态）
set -euo pipefail

docker rm -f pool-exhaustion-probe >/dev/null 2>&1 || true
echo "[pool-exhaustion] 清理完成：探针已停，池内连接随请求结束即时归还"
