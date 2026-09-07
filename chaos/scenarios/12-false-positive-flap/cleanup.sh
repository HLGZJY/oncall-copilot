#!/usr/bin/env bash
# 剧本 12 清理：恢复规则默认阈值（配置漂移型误报的处置 = 修正配置）+ 停流量探针
set -euo pipefail

RULES_FILE="deploy/prometheus/rules.yml"
BAK="/tmp/oncall-12-false-positive-flap-rules.yml.bak"
PROM_CONTAINER="${PROM_CONTAINER:-oncall-demo-prometheus-1}"

cd "$(dirname "$0")/../../.."  # 回仓库根

docker rm -f false-positive-flap-probe >/dev/null 2>&1 || true

# 1. 恢复规则：优先还原注入前备份（备份用后即删，避免下次误还原旧内容）；
#    备份丢失（/tmp 被清）则直接回写默认阈值——两条路径都幂等，可重复执行
if [ -f "$BAK" ]; then
  cp "$BAK" "$RULES_FILE"
  rm -f "$BAK"
elif grep -q ') > 0.01' "$RULES_FILE"; then
  sed -i 's/) > 0.01/) > 0.12/' "$RULES_FILE"
fi

# 2. 校验 + reload（幂等：已是默认态时 reload 无副作用）
docker exec "$PROM_CONTAINER" promtool check rules /etc/prometheus/rules.yml
docker kill -s HUP "$PROM_CONTAINER"

echo "[false-positive-flap] 清理完成：DemoTasksLatencyFlap 阈值已恢复默认 0.12s，流量探针已停止"
