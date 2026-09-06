#!/usr/bin/env bash
# 剧本 05 清理：停掉 Pumba（SIGTERM 触发 qdisc 清理）与探针
set -euo pipefail

docker rm -f packet-loss-probe >/dev/null 2>&1 || true

if docker ps -a --format '{{.Names}}' | grep -qx pumba-packet-loss; then
  docker stop -t 15 pumba-packet-loss >/dev/null
  docker rm pumba-packet-loss >/dev/null
fi

echo "[packet-loss] 清理完成：丢包规则已撤销，TCP 重传队列随后排空"
