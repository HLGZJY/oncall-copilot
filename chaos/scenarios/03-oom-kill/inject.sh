#!/usr/bin/env bash
# 剧本 03：OOM——api-gw 容器内存超限被内核 OOM killer 杀死（资源类）
#
# 注入手段偏离说明（m0-execution 原表写 Pumba）：Pumba 的 stress --vm 注入目标
# cgroup 后，超限时内核 OOM killer 杀的是 cgroup 内"坏度"最高的进程——通常是
# stress-ng 自己而非目标应用，故障不可观测。改用 cgroup memory.max 确定性触发：
# 把 api-gw 容器内存上限压到当前 RSS 的 ~60%，业务探针持续请求迫使分配越限，
# 内核 OOM kill 掉 PID 1（uvicorn）→ 容器退出（OOMKilled）→ Prometheus 抓取失联。
#
# 观测：up{job="demo-api-gw"} == 0 持续 2m → DemoApiGwDown → Alertmanager
#       → deploy/alerts-dump.jsonl；docker inspect 可见 State.OOMKilled=true
#
# 环境变量：LIMIT_RATIO（上限/RSS 比例，默认 0.6）
set -euo pipefail

API_CONTAINER="${API_CONTAINER:-oncall-demo-api-gw-1}"
RECORD="/tmp/oncall-demo-oom-kill.record"
LIMIT_RATIO="${LIMIT_RATIO:-0.6}"

# 1. 记录原始内存上限（恢复用）；记录文件已存在说明上次未清理，保留真实基线
if [[ ! -f "$RECORD" ]]; then
  docker inspect -f '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' \
    "$API_CONTAINER" > "$RECORD"
  echo "[oom-kill] 原始 memory/swap='$(cat "$RECORD")'（0 = 无限制）"
else
  echo "[oom-kill] 检测到未清理的上次注入，沿用既有基线记录"
fi

# 2. 读取当前 RSS，压低内存上限（不低于 32MB，防止秒杀到无法观测）
MEM_USAGE="$(docker stats --no-stream --format '{{.MemUsage}}' "$API_CONTAINER")"
USED_MB="$(echo "$MEM_USAGE" | sed -E 's/^([0-9.]+)([^/]+).*/\1 \2/' | awk '{if ($2 ~ /GiB/) printf "%d", $1*1024; else printf "%d", $1}')"
LIMIT_MB=$(( USED_MB > 64 ? USED_MB * 60 / 100 : 32 ))
echo "[oom-kill] 当前 RSS=${USED_MB}MB，压低上限到 ${LIMIT_MB}MB"
docker update --memory "${LIMIT_MB}M" --memory-swap "${LIMIT_MB}M" "$API_CONTAINER" >/dev/null

# 3. 业务探针：64 线程打 GET /health，迫使 uvicorn 持续分配直到越限被杀
docker rm -f oom-kill-probe >/dev/null 2>&1 || true
docker run -d --name oom-kill-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import time, urllib.request
from concurrent.futures import ThreadPoolExecutor
def hit(_):
    while True:
        try:
            urllib.request.urlopen('http://api-gw:8000/health', timeout=5)
        except Exception:
            pass
with ThreadPoolExecutor(64) as ex:
    list(ex.map(hit, range(64)))
"

echo "[oom-kill] 注入完成：等待内核 OOM killer 终结 api-gw（通常 <60s）"
echo "[oom-kill] 观察：docker inspect $API_CONTAINER | grep OOMKilled"
echo "[oom-kill]       curl 'http://127.0.0.1:9090/api/v1/query?query=ALERTS%7Balertname%3D%22DemoApiGwDown%22%7D'"
