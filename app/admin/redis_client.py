"""
app/admin/redis_client.py
=========================
Redis client factory with cluster-mode support.

Single-node mode (default):
    Uses a shared ConnectionPool backed by REDIS_URL.
    Suitable for development and single-instance deployments.

Cluster mode:
    Enabled by setting REDIS_CLUSTER_NODES to a comma-separated list
    of "host:port" pairs, e.g.:
        REDIS_CLUSTER_NODES=redis-node-1:6379,redis-node-2:6379,redis-node-3:6379

    Uses redis-py's RedisCluster client. The cluster handles slot
    routing automatically; application code is identical in both modes.
    Cluster mode eliminates the single Redis instance as a SPOF:
    a node failure only affects the key slots it owns, not the entire
    ingestion pipeline.

Usage (identical in both modes):
    from app.admin.redis_client import get_redis

    r = get_redis()
    r.set("key", "value", ex=60)
    r.ping()
"""

from __future__ import annotations

import logging
from typing import Union

import redis
import redis.cluster

from app.config import settings

logger = logging.getLogger(__name__)

# ============================================================
# CLIENT INITIALISATION
# ============================================================

_pool: redis.ConnectionPool | None = None
_cluster_client: redis.cluster.RedisCluster | None = None


def _init_client() -> None:
    """
    Initialise the appropriate Redis client based on config.
    Called once at module import time.
    """
    global _pool, _cluster_client

    if settings.redis_cluster_enabled:
        # Parse "host:port,host:port,..." into startup nodes
        startup_nodes = []
        for node in settings.redis_cluster_nodes.split(","):
            node = node.strip()
            if not node:
                continue
            host, _, port = node.rpartition(":")
            startup_nodes.append(
                redis.cluster.ClusterNode(host=host, port=int(port or 6379))
            )

        if not startup_nodes:
            raise RuntimeError(
                "REDIS_CLUSTER_NODES is set but contains no valid 'host:port' entries."
            )

        _cluster_client = redis.cluster.RedisCluster(
            startup_nodes=startup_nodes,
            decode_responses=True,
            skip_full_coverage_check=True,   # allow partial coverage in dev clusters
        )
        logger.info(
            "Redis cluster client initialised | nodes=%d",
            len(startup_nodes),
        )

    else:
        _pool = redis.ConnectionPool.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
        )
        logger.info(
            "Redis single-node pool initialised | url=%s | max_connections=%d",
            settings.redis_url,
            settings.redis_max_connections,
        )


_init_client()


# ============================================================
# PUBLIC FACTORY
# ============================================================

def get_redis() -> Union[redis.Redis, redis.cluster.RedisCluster]:
    """
    Return a Redis client.

    In single-node mode: returns a Redis instance backed by the
    shared connection pool. Call this per-request or per-function;
    do not store the client as a module-level singleton.

    In cluster mode: returns the shared RedisCluster client, which
    is thread-safe and manages its own internal connection pools
    per cluster node.

    Usage:
        r = get_redis()
        r.set("key", "value", ex=60)
    """
    if settings.redis_cluster_enabled:
        return _cluster_client

    return redis.Redis(connection_pool=_pool)


# ============================================================
# KEY BUILDERS
# Centralised — changing a key pattern here fixes it everywhere.
# ============================================================

def dedup_key(payload_hash: str) -> str:
    """Deduplication window key. TTL: 24 h."""
    return f"dedup:{payload_hash}"


def volume_key(customer_id: str) -> str:
    """Per-customer request counter. TTL: 5 min."""
    return f"vol:{customer_id}"


def circuit_key(service_name: str) -> str:
    """Circuit breaker flag. No TTL — manually closed or auto-recovered."""
    return f"circuit:{service_name}"


def cache_key(vector_hash: str) -> str:
    """Semantic cache bypass key. TTL: 1 h."""
    return f"semcache:{vector_hash}"


# ============================================================
# HEALTH CHECK
# ============================================================

def ping() -> bool:
    """
    Returns True if Redis is reachable, False otherwise.
    Used by /health and /system-status endpoints.
    """
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
