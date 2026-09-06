#!/usr/bin/env bash
# 剧本 04：下游超时——api-gw 网络被注入 700ms 固定延迟（网络类 / Pumba netem）
#
# 机制：Pumba netem 对 api-gw 出方向全部流量加固定延迟。探针请求的 DB 调用
#       往返多耗 ~0.7s，/tasks P95 越过 0.5s 阈值，连接池随之被占满。
#       延迟取 700ms 是实测权衡（见下"教训"）：足够顶过告警阈值，又不至于
#       超过 Prometheus 抓取超时（scrape_timeout=4s）弄瞎观测面。
#
# 实测教训（Docker Desktop / WSL2，issue 04）：
#   1. pumba --ingress-port/--egress-port 端口过滤在本环境不生效——按端口只延迟
#      mysql 流量的两种目标（api-gw 出向、mysql 入向）实测都是"过滤被忽略"。
#      3s 全流量延迟则会把抓取响应也拖过 4s 超时 → up=0 → DemoApiGwDown 误触发、
#      真实告警失明（观测面与故障共沉浮的反面教材）。
#   2. pumba 语义：--duration 必须 < --interval（按 interval 周期重施 netem）；
#      cleanup 用 SIGTERM 提前撤销。
#
# 观测：12 线程探针并发 POST /tasks：
#       - /tasks P95 ~0.7s 越过 0.5s → DemoTasksHighLatency
#       - 12 并发长期占住连接池（5+5）→ DemoDbPoolSaturated
#       - up 保持 1（0.7s < 抓取超时 4s），观测面完整
#
# 环境变量：DURATION_SEC（秒，默认 300）、DELAY_MS（默认 700）
set -euo pipefail

DURATION_SEC="${DURATION_SEC:-300}"
DURATION="${DURATION_SEC}s"
DELAY_MS="${DELAY_MS:-700}"

# 注意 pumba 镜像 entrypoint 即 pumba，命令里不能再带 pumba 前缀（issue 03 坑）
docker rm -f pumba-downstream-timeout >/dev/null 2>&1 || true
docker run -d --name pumba-downstream-timeout \
  -v /var/run/docker.sock:/var/run/docker.sock \
  gaiaadm/pumba:latest \
  --interval 330s \
  netem --duration "$DURATION" \
  delay --time "$DELAY_MS" \
  re2:^oncall-demo-api-gw-1$

# 业务探针：12 线程并发 POST /tasks
docker rm -f downstream-timeout-probe >/dev/null 2>&1 || true
docker run -d --name downstream-timeout-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
end = time.time() + ${DURATION_SEC}
def hit(_):
    while time.time() < end:
        try:
            req = urllib.request.Request(
                'http://api-gw:8000/tasks',
                data=json.dumps({'payload': 'downstream-timeout-probe'}).encode(),
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=40)
        except Exception:
            pass
        time.sleep(0.5)
with ThreadPoolExecutor(12) as ex:
    list(ex.map(hit, range(12)))
"

echo "[downstream-timeout] 注入完成：api-gw 全部出口流量延迟 ${DELAY_MS}ms，持续 ${DURATION_SEC}s"
echo "[downstream-timeout] 观察：curl 'http://127.0.0.1:9090/api/v1/query?query=ALERTS%7Balertname%3D%22DemoTasksHighLatency%22%7D'"
