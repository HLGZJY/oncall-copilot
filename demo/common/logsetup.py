"""demo 共享日志配置：structlog JSON 渲染到 stdout，由 Docker loki driver 直推 Loki。"""

import logging

import structlog


def configure_logging(service: str) -> None:
    """两个服务共用；service 名进每条日志字段，便于 Loki 按 service 过滤。"""
    logging.basicConfig(level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
    structlog.get_logger(service).bind(service=service).info("logging configured")
