"""
tests/test_logging_middleware.py
=================================
Unit tests for correlation ID propagation and log filter.
"""

import logging
import pytest

from app.middleware.logging_middleware import (
    CorrelationIdFilter,
    get_correlation_id,
    set_correlation_id,
)


class TestCorrelationIdContextVar:

    def test_default_is_empty(self):
        set_correlation_id("")
        assert get_correlation_id() == ""

    def test_set_and_get(self):
        test_id = "test-correlation-123"
        set_correlation_id(test_id)
        assert get_correlation_id() == test_id

    def test_overwrite(self):
        set_correlation_id("first")
        set_correlation_id("second")
        assert get_correlation_id() == "second"


class TestCorrelationIdFilter:

    def test_filter_injects_correlation_id(self):
        set_correlation_id("req-abc-456")
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        f = CorrelationIdFilter()
        f.filter(record)
        assert record.correlation_id == "req-abc-456"

    def test_filter_uses_dash_when_empty(self):
        set_correlation_id("")
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        f = CorrelationIdFilter()
        f.filter(record)
        assert record.correlation_id == "-"

    def test_filter_always_returns_true(self):
        record = logging.LogRecord(
            name="test", level=logging.DEBUG,
            pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        f = CorrelationIdFilter()
        assert f.filter(record) is True
