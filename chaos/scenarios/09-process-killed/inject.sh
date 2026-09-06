#!/usr/bin/env bash
# 剧本 09：进程被杀——Pumba 向 worker 主进程发 SIGKILL（故障类 / Pumba kill）
#
# 机制：pumba kill 直接 SIGKILL worker 容器的 PID 1（celery 主进程），
#       容器以 137 退出且无重启策略——消费能力瞬间归零，提交侧还在灌任务：
#       队列净增速 ≈ 探针速率。与剧本 03（OOM 杀 api-gw）区分：这里杀的是
#       消费端，业务面表现是"提交正常但任务永远不完成"。
#
# 观测：12 线程探针持续 POST /tasks → demo_queue_depth > 20 持续 2m
#       → DemoQueueDepthHigh；docker inspect State.ExitCode=137
#
# 环境变量：DURATION_SEC（秒，默认 300）
set -euo pipefail

DURATION_SEC="${DURATION_SEC:-300}"
WORKER_CONTAINER="${WORKER_CONTAINER:-oncall-demo-worker-1}"

# 1. Pumba kill（无 --interval 时默认循环值守，cleanup 先停 pumba 再拉起 worker）
docker rm -f pumba-process-killed >/dev/null 2>&1 || true
docker run -d --name pumba-process-killed \
  -v /var/run/docker.sock:/var/run/docker.sock \
  gaiaadm/pumba:latest \
  kill --signal SIGKILL \
  re2:^oncall-demo-worker-1$

# 2. 业务探针：12 线程持续提交（消费端已死，全部积压）
docker rm -f process-killed-probe >/dev/null 2>&1 || true
docker run -d --name process-killed-probe --network oncall-demo_default \
  python:3.11-slim python -c "
import json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
end = time.time() + ${DURATION_SEC}
def hit(_):
    while time.time() < end:
        try:
            req = urllib.request.Request(
                'http://api-gw:8000/tasks',
                data=json.dumps({'payload': 'process-killed-probe'}).encode(),
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass
        time.sleep(0.5)
with ThreadPoolExecutor(12) as ex:
    list(ex.map(hit, range(12)))
"

echo "[process-killed] 注入完成：worker 已被 SIGKILL（ExitCode=137），12 线程探针持续提交"
echo "[process-killed] 观察：docker inspect $WORKER_CONTAINER -f '{{.State.ExitCode}} {{.State.OOMKilled}}'"
echo "[process-killed]       curl 'http://127.0.0.1:9090/api/v1/query?query=ALERTS%7Balertname%3D%22DemoQueueDepthHigh%22%7D'"
