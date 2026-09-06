#!/usr/bin/env bash
# 剧本 05：网络丢包——api-gw 到 MySQL 的流量 25% 丢包（网络类 / Pumba netem）
#
# 机制：对 api-gw 出方向全部流量随机丢 25% 的包。TCP 自动重传掩盖了
#       "连接失败"，但每次丢包换来 200ms~1s 的 RTO 重传等待——查询延迟陡增，
#       连接池里的连接被重传期长期占住。
#       与剧本 04 的区分：延迟是"每个包恒定慢"，丢包是"随机慢 + 尾部抖动"，
#       M3 排查时指标形态不同（P95 尖刺 vs 整体抬升）。
#       实测教训（同剧本 04）：Docker Desktop/WSL2 上 pumba 的端口过滤不生效，
#       只能全流量丢包；25% 丢包对小包抓取的 TCP 重传在抓取超时（4s）内，up 保持 1。
#
# 观测：12 线程探针并发 POST /tasks → /tasks P95 越过 0.5s → DemoTasksHighLatency
#
# 环境变量：DURATION_SEC（秒，默认 300）、LOSS_PCT（默认 25）
set -euo pipefail

DURATION_SEC="${DURATION_SEC:-300}"
DURATION="${DURATION_SEC}s"
LOSS_PCT="${LOSS_PCT:-25}"

docker rm -f pumba-packet-loss >/dev/null 2>&1 || true
docker run -d --name pumba-packet-loss \
  -v /var/run/docker.sock:/var/run/docker.sock \
  gaiaadm/pumba:latest \
  --interval 330s \
  netem --duration "$DURATION" \
  loss --percent "$LOSS_PCT" \
  re2:^oncall-demo-api-gw-1$

docker rm -f packet-loss-probe >/dev/null 2>&1 || true
docker run -d --name packet-loss-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
end = time.time() + ${DURATION_SEC}
def hit(_):
    while time.time() < end:
        try:
            req = urllib.request.Request(
                'http://api-gw:8000/tasks',
                data=json.dumps({'payload': 'packet-loss-probe'}).encode(),
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=40)
        except Exception:
            pass
        time.sleep(0.5)
with ThreadPoolExecutor(12) as ex:
    list(ex.map(hit, range(12)))
"

echo "[packet-loss] 注入完成：api-gw→mysql 丢包 ${LOSS_PCT}%，持续 ${DURATION_SEC}s"
echo "[packet-loss] 观察：curl 'http://127.0.0.1:9090/api/v1/query?query=ALERTS%7Balertname%3D%22DemoTasksHighLatency%22%7D'"
