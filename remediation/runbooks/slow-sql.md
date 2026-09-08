---
slug: slow-sql
alert_ref: DemoDbPoolSaturated
severity: warning
actions:
  - id: kill-lock-session
    name: KILL 持锁会话释放 tasks 表锁
    steps:
      - action: docker.remove_container
        params:
          name: slow-sql-probe
      - action: mysql.kill_session
        params:
          container: oncall-demo-mysql-1
          session_id: $session_id
rollback:
  - action: mysql.kill_session
    params:
      container: oncall-demo-mysql-1
      session_id: $session_id
verification:
  promql: 'avg_over_time(demo_db_pool_used[1m])'
  condition: db_pool_used < db_pool_size
  window_s: 60
---
# slow-sql 处置说明

触发告警：DemoDbPoolSaturated（DB 连接池持续高占用，1m 平均 >= 70% 池容量）。
根因（golden root_cause）：注入会话对 tasks 表持 LOCK TABLES WRITE 并 SLEEP，
api-gw 全部 tasks 读写阻塞在表锁上，连接池随之打满。

处置 = chaos/scenarios/02-slow-sql/cleanup.sh 语义（cleanup 动作 → 白名单原子操作引用，
执行只消费白名单引用，本正文仅作模型上下文说明）：

1. 停掉业务探针容器 slow-sql-probe（docker.remove_container，若还在跑）。
2. KILL 持有 SLEEP 的会话（即注入会话）（mysql.kill_session，container=oncall-demo-mysql-1，
   session_id 由执行器在干跑/受控执行期解析注入）——锁随之释放。

恢复判据（verification，与 golden remediation 实测回落值对齐）：
KILL 释放表锁后，阻塞请求在连接池排空窗（golden ≤35s 排空口径，折算进 60s 观察窗）内
被消费，`demo_db_pool_used` 回落至池容量（demo_db_pool_size）之内并保持，观察窗 60s。

回滚预案（rollback，D-45 显式定义）：KILL 是幂等操作，未恢复时按 cleanup.sh 幂等语义
重试 kill_session（重复 KILL 同一会话无害）；若二次仍失败 → 转人工（D-28，不是丢弃）。
