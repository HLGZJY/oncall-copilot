"""demo 业务指标（issue 02 四类观测契约）：QPS / 延迟直方图 / 队列深度 / DB 连接池。

命名遵循 prometheus 惯例；`demo_` 前缀区分业务系统指标与将来 oncall 自身指标。
队列深度/连接池是 Gauge，由 /metrics 出口前 refresh_gauges() 采样。
"""

from prometheus_client import Counter, Gauge, Histogram

REQUESTS_TOTAL = Counter(
    "demo_requests_total",
    "Total HTTP requests (source of QPS via rate())",
    ["method", "endpoint", "status"],
)
REQUEST_DURATION_SECONDS = Histogram(
    "demo_request_duration_seconds",
    "Request latency in seconds",
    ["endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
QUEUE_DEPTH = Gauge("demo_queue_depth", "Pending tasks in Celery broker queue")
DB_POOL_USED = Gauge("demo_db_pool_used", "SQLAlchemy connections currently checked out")
DB_POOL_SIZE = Gauge("demo_db_pool_size", "SQLAlchemy pool base size")
# 剧本 10（缓存雪崩）：任务查询缓存命中/未命中计数
TASK_CACHE_OPERATIONS = Counter(
    "demo_task_cache_operations_total",
    "Task read cache operations by result",
    ["result"],
)
# 剧本 11（版本协议不兼容）：业务滞留指标——pending 超 60s 的任务数，
# 指标日志全正常但业务停摆的语义层故障靠它告警
STALE_PENDING = Gauge(
    "demo_tasks_stale_pending",
    "Tasks stuck in pending status for more than 60 seconds",
)
