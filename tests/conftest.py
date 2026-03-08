"""
tests/conftest.py
=================
Shared pytest fixtures for the Kirana Kart test suite.

Fixtures here are available to all test modules without explicit imports.
"""

import pytest
from unittest.mock import MagicMock, patch


# ============================================================
# ENVIRONMENT ISOLATION
# ============================================================

@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """
    Ensure tests do not accidentally read the real .env file.
    Sets minimal safe defaults for all config values.
    """
    monkeypatch.setenv("DB_HOST",     "localhost")
    monkeypatch.setenv("DB_PORT",     "5432")
    monkeypatch.setenv("DB_NAME",     "test_db")
    monkeypatch.setenv("DB_USER",     "test_user")
    monkeypatch.setenv("DB_PASSWORD", "test_pass")
    monkeypatch.setenv("REDIS_URL",   "redis://localhost:6379/9")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
    monkeypatch.setenv("LOG_FORMAT",  "text")


# ============================================================
# MOCK FIXTURES
# ============================================================

@pytest.fixture
def mock_redis():
    """Return a MagicMock that behaves like a redis.Redis client."""
    r = MagicMock()
    r.ping.return_value = True
    r.get.return_value = None
    r.set.return_value = True
    r.exists.return_value = 0
    return r


@pytest.fixture
def mock_db_session():
    """Return a MagicMock that behaves like a SQLAlchemy Session."""
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session
