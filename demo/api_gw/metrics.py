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
