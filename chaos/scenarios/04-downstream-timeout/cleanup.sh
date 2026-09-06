#!/usr/bin/env bash
# 剧本 04 清理：停掉 Pumba（SIGTERM 触发其清理目标容器上的 tc qdisc）与探针
# 注意用 stop 而非 rm -f：SIGKILL 会跳过 pumba 的 qdisc 清理钩子
set -euo pipefail

docker rm -f downstream-timeout-probe >/dev/null 2>&1 || true

if docker ps -a --format '{{.Names}}' | grep -qx pumba-downstream-timeout; then
  docker stop -t 15 pumba-downstream-timeout >/dev/null
  docker rm pumba-downstream-timeout >/dev/null
fi

echo "[downstream-timeout] 清理完成：netem 延迟已撤销，在途请求将在超时窗口内排空"
