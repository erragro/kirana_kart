"""
tests/test_redis_client.py
==========================
Unit tests for app/admin/redis_client.py

Tests key builders and the health-check helper. The Redis connection
itself is mocked — no live Redis required.
"""

import pytest
from unittest.mock import patch, MagicMock

from app.admin.redis_client import (
    dedup_key,
    volume_key,
    circuit_key,
    cache_key,
)


# ============================================================
# KEY BUILDERS
# ============================================================

class TestKeyBuilders:

    def test_dedup_key_format(self):
        assert dedup_key("abc123") == "dedup:abc123"

    def test_volume_key_format(self):
        assert volume_key("CUST-001") == "vol:CUST-001"

    def test_circuit_key_format(self):
        assert circuit_key("weaviate") == "circuit:weaviate"

    def test_cache_key_format(self):
        assert cache_key("hashval") == "semcache:hashval"

    def test_dedup_key_preserves_hash(self):
        h = "a" * 64   # SHA256 hex length
        assert dedup_key(h) == f"dedup:{h}"

    def test_volume_key_with_uuid_customer(self):
        cid = "550e8400-e29b-41d4-a716-446655440000"
        assert volume_key(cid) == f"vol:{cid}"


# ============================================================
# PING HEALTH CHECK
# ============================================================

class TestPing:

    def test_ping_returns_true_when_redis_up(self):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        with patch("app.admin.redis_client.get_redis", return_value=mock_client):
            from app.admin.redis_client import ping
            assert ping() is True

    def test_ping_returns_false_when_redis_down(self):
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("Connection refused")
        with patch("app.admin.redis_client.get_redis", return_value=mock_client):
            from app.admin.redis_client import ping
            assert ping() is False
