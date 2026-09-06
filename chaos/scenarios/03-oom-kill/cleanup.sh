#!/usr/bin/env bash
# 剧本 03 清理：恢复 api-gw 内存上限并拉起容器（OOM 后容器处于 exited 状态）
set -euo pipefail

API_CONTAINER="${API_CONTAINER:-oncall-demo-api-gw-1}"
RECORD="/tmp/oncall-demo-oom-kill.record"

# 0. 停探针
docker rm -f oom-kill-probe >/dev/null 2>&1 || true

# 1. 恢复内存上限；基线为 0（无限制）时兜底设为宿主总内存
MEM="$(docker info -f '{{.MemTotal}}')"
MEM_MB=$(( MEM / 1048576 ))
ORIG_MEM=0
if [[ -f "$RECORD" ]]; then
  ORIG_MEM="$(cut -d' ' -f1 "$RECORD")"
  rm -f "$RECORD"
fi
if [[ "$ORIG_MEM" == "0" || -z "$ORIG_MEM" ]]; then
  echo "[oom-kill] 基线无限制，恢复为宿主总内存 ${MEM_MB}M"
  docker update --memory "${MEM_MB}M" --memory-swap "${MEM_MB}M" "$API_CONTAINER" >/dev/null
else
  ORIG_SWAP="$(cut -d' ' -f2 "$RECORD" 2>/dev/null || echo "$ORIG_MEM")"
  ORIG_SWAP="${ORIG_SWAP:-0}"
  [[ "$ORIG_SWAP" == "0" ]] && ORIG_SWAP="$ORIG_MEM"
  docker update --memory "$(( ORIG_MEM / 1048576 ))M" --memory-swap "$(( ORIG_SWAP / 1048576 ))M" \
    "$API_CONTAINER" >/dev/null
fi

# 2. OOM 后容器已退出：重新拉起
STATE="$(docker inspect -f '{{.State.Status}}' "$API_CONTAINER")"
if [[ "$STATE" != "running" ]]; then
  docker start "$API_CONTAINER" >/dev/null
  echo "[oom-kill] api-gw 已重新启动，等待健康检查通过（约 30s）"
fi

echo "[oom-kill] 清理完成：内存上限已恢复，服务已拉起"
