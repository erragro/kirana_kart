import os
import redis
from pathlib import Path
from dotenv import load_dotenv

# ============================================================
# ENVIRONMENT SETUP
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
env_path = PROJECT_ROOT / ".env"

if not env_path.exists():
    raise RuntimeError(".env file not found in project root")

load_dotenv(dotenv_path=env_path)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ============================================================
# CONNECTION POOL
# Shared pool — imported once, reused across all phases.
# Pool size 10 is sufficient for local dev. Raise to 20-50
# on production worker containers.
# ============================================================

_pool = redis.ConnectionPool.from_url(
    REDIS_URL,
    max_connections=10,
    decode_responses=True      # all keys/values come back as str, not bytes
)


def get_redis() -> redis.Redis:
    """
    Return a Redis client backed by the shared connection pool.
    Call this inside each phase function — do not store the
    client as a module-level singleton.

    Usage:
        from app.admin.redis_client import get_redis
        r = get_redis()
        r.set("key", "value", ex=60)
    """
    return redis.Redis(connection_pool=_pool)


# ============================================================
# KEY BUILDERS
# Centralised so every phase uses identical key formats.
# Changing a key pattern here fixes it everywhere.
# ============================================================

def dedup_key(payload_hash: str) -> str:
    """
    Deduplication check key.
    TTL: 24h (set by phase2_deduplicator).
    """
    return f"dedup:{payload_hash}"


def volume_key(customer_id: str) -> str:
    """
    Per-customer request counter key.
    TTL: 5 min (set by phase4_enricher).
    """
    return f"vol:{customer_id}"


def circuit_key(service_name: str) -> str:
    """
    Circuit breaker flag key.
    TTL: none — manually closed or auto-recovered.
    Services: 'weaviate' | 'openai' | 'llm_stage_1' etc.
    """
    return f"circuit:{service_name}"


def cache_key(vector_hash: str) -> str:
    """
    Semantic cache key for Weaviate bypass results.
    TTL: 1h for live data (set by phase4_enricher).
    """
    return f"semcache:{vector_hash}"


# ============================================================
# HEALTH CHECK
# ============================================================

def ping() -> bool:
    """
    Returns True if Redis is reachable, False otherwise.
    Used by /system-status endpoint.
    """
    try:
        return get_redis().ping()
    except Exception:
        return False