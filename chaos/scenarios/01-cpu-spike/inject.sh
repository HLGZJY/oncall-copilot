#!/usr/bin/env bash
# 剧本 01：CPU 飙高（资源类 / Pumba）
#
# 机制（三件套）：
#   1. 运行时把 api-gw 容器收窄到单核（docker update --cpuset-cpus=0，不重建容器）；
#   2. Pumba 用 --inject-cgroup 把 stress-ng 注入 api-gw 的 cgroup 打满该核；
#   3. 单容器 128 线程业务探针打 GET /health 制造排队——**空载的 I/O 型端点在 CFS
#      公平调度下饿不出延迟**（实测无流量时 P95 仅 ~10ms），必须叠加业务流量才可观测。
#   收窄 cpuset 是为了让"共享全核的宿主机"上也能稳定复现，属演示环境放大器。
#
# 观测：demo_request_duration_seconds（/health /metrics 等非 DB 路径）P95 从 ~5ms
#       涨到 ~95ms，越过 0.05s → 触发 DemoApiGwHighLatency → Alertmanager
#       → deploy/alerts-dump.jsonl（直方图为服务端计时，必须叠加探针流量）
#
# 环境变量：DURATION_SEC（秒，默认 300；需覆盖告警 for:1m + 评估/抓取延迟）
set -euo pipefail

DURATION_SEC="${DURATION_SEC:-300}"
DURATION="${DURATION_SEC}s"
API_CONTAINER="${API_CONTAINER:-oncall-demo-api-gw-1}"
CPUSET_RECORD="$(mktemp /tmp/oncall-demo-cpu-spike.XXXXXX)"

# 1. 记录原始 cpuset（cleanup 恢复用），再收窄到 CPU 0
#    记录文件已存在说明上次注入未清理，保留最早的真实基线，防止把 '0' 当基线
if [[ ! -f /tmp/oncall-demo-cpu-spike.record ]]; then
  docker inspect -f '{{.HostConfig.CpusetCpus}}' "$API_CONTAINER" > "$CPUSET_RECORD"
  echo "[cpu-spike] 原始 cpuset='$(cat "$CPUSET_RECORD")'，收窄到 CPU 0"
  echo "$CPUSET_RECORD" > /tmp/oncall-demo-cpu-spike.record
else
  echo "[cpu-spike] 检测到未清理的上次注入，沿用既有 cpuset 基线记录"
fi
docker update --cpuset-cpus=0 "$API_CONTAINER" >/dev/null

# 2. Pumba cpu stress：stress-ng 注入目标 cgroup，打满收窄后的单核
docker rm -f pumba-cpu-spike >/dev/null 2>&1 || true
docker run -d --name pumba-cpu-spike \
  -v /var/run/docker.sock:/var/run/docker.sock \
  gaiaadm/pumba:latest \
  stress \
    --duration "$DURATION" \
    --stressors "--cpu 2 --timeout $DURATION" \
    --inject-cgroup \
    "$API_CONTAINER"

# 3. 业务探针：单容器 128 线程打 GET /health，制造排队流量
#    （不用宿主机循环 curl：Windows/git bash 大量派生进程不可靠）
docker rm -f cpu-spike-probe >/dev/null 2>&1 || true
docker run -d --name cpu-spike-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import time, urllib.request
from concurrent.futures import ThreadPoolExecutor
end = time.time() + ${DURATION_SEC}
def hit(_):
    while time.time() < end:
        try:
            urllib.request.urlopen('http://api-gw:8000/health', timeout=10)
        except Exception:
            pass
with ThreadPoolExecutor(128) as ex:
    list(ex.map(hit, range(128)))
"

echo "[cpu-spike] 注入完成：stress-ng 已进入 cgroup，128 线程探针运行 ${DURATION_SEC}s"
echo "[cpu-spike] 观察：curl 'http://127.0.0.1:9090/api/v1/query?query=ALERTS%7Balertname%3D%22DemoApiGwHighLatency%22%7D'"
