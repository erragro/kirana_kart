"""
app/middleware/logging_middleware.py
=====================================
Structured logging setup + correlation ID propagation middleware.

Structured logging:
    Uses python-json-logger to emit JSON log lines in production
    (LOG_FORMAT=json) or human-readable text in development
    (LOG_FORMAT=text). Every log record includes:
        timestamp, level, logger, message, correlation_id

Correlation ID:
    Each inbound HTTP request is assigned a correlation_id UUID.
    If the caller supplies X-Correlation-ID it is reused (useful for
    tracing a request across service boundaries). The ID is:
        - Stored in a ContextVar so it propagates to any function called
          during the request without explicit threading.
        - Added to every log record via a logging.Filter.
        - Echoed back in the X-Correlation-ID response header.

Usage — access the current ID anywhere in the call stack:
    from app.middleware.logging_middleware import get_correlation_id

    logger.info("msg", extra={"order_id": order_id})
    # → JSON: {..., "correlation_id": "abc-123", "order_id": "ORD-99"}
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

# ============================================================
# CONTEXT VAR — one ID per async task / thread
# ============================================================

_correlation_id_var: ContextVar[str] = ContextVar(
    "correlation_id", default=""
)


def get_correlation_id() -> str:
    """Return the correlation ID for the current request context."""
    return _correlation_id_var.get()


def set_correlation_id(value: str) -> None:
    _correlation_id_var.set(value)


# ============================================================
# LOG FILTER — injects correlation_id into every record
# ============================================================

class CorrelationIdFilter(logging.Filter):
    """Adds correlation_id to every LogRecord automatically."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id() or "-"
        return True


# ============================================================
# LOGGING SETUP — call once at application startup
# ============================================================

def configure_logging() -> None:
    """
    Configure root logger with JSON or text format.

    Called in app startup (lifespan). Safe to call multiple times
    (idempotent — checks if handlers already attached).
    """
    root = logging.getLogger()

    if root.handlers:
        return  # already configured

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)

    handler = logging.StreamHandler()
    handler.addFilter(CorrelationIdFilter())

    if settings.log_format.lower() == "json":
        try:
            from pythonjsonlogger import jsonlogger
            fmt = jsonlogger.JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s %(correlation_id)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
                rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
            )
            handler.setFormatter(fmt)
        except ImportError:
            # Fallback if python-json-logger not installed yet
            _set_text_formatter(handler)
    else:
        _set_text_formatter(handler)

    root.addHandler(handler)


def _set_text_formatter(handler: logging.StreamHandler) -> None:
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s [%(correlation_id)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(fmt)


# ============================================================
# FASTAPI MIDDLEWARE
# ============================================================

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    HTTP middleware that:
    1. Reads X-Correlation-ID from inbound request (or generates a UUID4).
    2. Sets the ContextVar so all log calls in the request share the ID.
    3. Adds X-Correlation-ID to the response headers.
    4. Logs request start/completion with method, path, status, duration.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Prefer caller-supplied ID so distributed traces stay connected.
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or str(uuid.uuid4())
        )
        set_correlation_id(correlation_id)

        logger = logging.getLogger("kirana_kart.http")
        start = time.perf_counter()

        logger.info(
            "Request started",
            extra={
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else "unknown",
            },
        )

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        logger.info(
            "Request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        response.headers["X-Correlation-ID"] = correlation_id
        return response
