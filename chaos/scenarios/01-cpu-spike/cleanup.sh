#!/usr/bin/env bash
# 剧本 01 清理：停掉 stress-ng 助手、恢复 api-gw 原始 cpuset
set -euo pipefail

API_CONTAINER="${API_CONTAINER:-oncall-demo-api-gw-1}"

# 0. 停业务探针容器（若还在跑）
docker rm -f cpu-spike-probe >/dev/null 2>&1 || true
rm -f /tmp/oncall-demo-cpu-spike.probe.pids

# 1. 停 Pumba/stress-ng（stress-ng 在目标 cgroup 内，容器停了进程也随之终止）
docker rm -f pumba-cpu-spike >/dev/null 2>&1 || true

# 2. 恢复原始 cpuset；记录文件丢失时兜底为全核
ORIG=""
if [[ -f /tmp/oncall-demo-cpu-spike.record ]]; then
  RECORD_FILE="$(cat /tmp/oncall-demo-cpu-spike.record)"
  ORIG="$(cat "$RECORD_FILE" 2>/dev/null || true)"
  rm -f "$RECORD_FILE" /tmp/oncall-demo-cpu-spike.record
fi
if [[ -z "$ORIG" ]]; then
  NCPU="$(docker info -f '{{.NCPU}}')"
  ORIG="0-$((NCPU - 1))"
fi
docker update --cpuset-cpus="$ORIG" "$API_CONTAINER" >/dev/null
echo "[cpu-spike] 清理完成：cpuset 恢复为 '$ORIG'"
