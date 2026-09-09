---
slug: cpu-spike
alert_ref: DemoApiGwHighLatency
severity: warning
actions:
  - id: stop-stress-and-restore-cpuset
    name: 停探针与 Pumba/stress-ng 并恢复 api-gw cpuset
    steps:
      - action: docker.remove_container
        params:
          name: cpu-spike-probe
      - action: docker.remove_container
        params:
          name: pumba-cpu-spike
      - action: docker.restore_cpuset
        params:
          container: oncall-demo-api-gw-1
          cores: $cpuset_cores
rollback: []
verification:
  promql: 'histogram_quantile(0.95, sum by (le) (rate(demo_request_duration_seconds_bucket{endpoint!="/tasks"}[1m])))'
  condition: p95 <= 0.05
  window_s: 60
---
# cpu-spike 处置说明

触发告警：DemoApiGwHighLatency（api-gw 非 DB 路径 P95 延迟 > 0.05s）。
根因（golden root_cause）：Pumba 把 stress-ng 注入 api-gw cgroup 打满其唯一 CPU 核，
业务流量下 uvicorn 事件循环与线程池排队，非 DB 路径 P95 从 ~5ms 涨到 ~95ms。

处置 = chaos/scenarios/01-cpu-spike/cleanup.sh 语义（cleanup 动作 → 白名单原子操作引用，
执行只消费白名单引用，本正文仅作模型上下文说明）：

1. 停掉业务探针容器 cpu-spike-probe（docker.remove_container，若还在跑）。
2. 停掉 Pumba/stress-ng 容器 pumba-cpu-spike —— stress-ng 在目标 cgroup 内，
   容器停后进程随之终止。
3. 恢复 api-gw（oncall-demo-api-gw-1）原始 cpuset（docker.restore_cpuset）：
   原 cpuset 从记录文件读取（执行期注入 `$cpuset_cores`，与 issue 05 运行时
   注入裁决一致），记录文件丢失时兜底为全核。

恢复判据（verification，与 golden remediation 实测回落值对齐）：
处置后观察非 DB 路径 P95 回落至 0.05s 以内（即回到告警阈值之下），观察窗 60s。
处置即恢复、无「再失败可回滚」预案 → rollback 显式为空（[]，D-45）；
恢复验证失败路径 = 直接转人工（D-28），不是 bug。
