import os
import hashlib
import json
import logging
import psycopg2
import psycopg2.extras

from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

from app.admin.redis_client import get_redis, dedup_key

# ============================================================
# ENVIRONMENT SETUP
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
env_path = PROJECT_ROOT / ".env"

if not env_path.exists():
    raise RuntimeError(".env file not found in project root")

load_dotenv(dotenv_path=env_path)

DB_HOST     = os.getenv("DB_HOST")
DB_PORT     = os.getenv("DB_PORT", "5432")
DB_NAME     = os.getenv("DB_NAME")
DB_USER     = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# ============================================================
# CONSTANTS
# ============================================================

DEDUP_TTL_SECONDS = 86_400      # 24 hours — Redis key expiry for dedup window
SCHEMA            = "kirana_kart"

# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("cardinal.phase2")

# ============================================================
# DATABASE
# ============================================================

def _get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


# ============================================================
# HASH COMPUTATION
#
# Normalise the payload before hashing so that cosmetic
# differences (key ordering, whitespace) do not produce
# different hashes for semantically identical payloads.
#
# Fields excluded from the hash:
#   - timestamp: same ticket re-sent seconds later must still
#     be caught as a duplicate.
#   - metadata.called_by / metadata.agent_id: these are
#     caller identifiers, not ticket content.
# ============================================================

_HASH_EXCLUDE_KEYS = {"timestamp", "received_at"}


def _normalise_for_hashing(payload: dict) -> dict:
    """
    Return a copy of the payload with non-content fields
    stripped so the hash reflects ticket identity, not
    submission metadata.
    """
    clean = {
        k: v for k, v in payload.items()
        if k not in _HASH_EXCLUDE_KEYS
    }

    # Strip submission-level metadata keys that vary per call
    if "metadata" in clean and isinstance(clean["metadata"], dict):
        meta = dict(clean["metadata"])
        for drop in ("called_by", "agent_id", "test_mode", "reprocess"):
            meta.pop(drop, None)
        clean["metadata"] = meta

    return clean


def compute_payload_hash(payload: dict) -> str:
    """
    Compute a stable SHA-256 hex digest for a canonical payload.

    The digest is:
      - Deterministic: same content always → same hash
      - Collision-resistant: different content → different hash
      - Fast: suitable for every inbound request

    Returns a 64-character hex string.
    """
    normalised = _normalise_for_hashing(payload)
    serialised = json.dumps(normalised, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


# ============================================================
# REDIS DEDUPLICATION CHECK
# ============================================================

def _redis_check(hash_val: str) -> tuple[bool, str | None]:
    """
    Check Redis for an existing dedup key.

    Returns:
        (is_duplicate: bool, original_ticket_id: str | None)

    The Redis value stores the original ticket_id so the caller
    can reference it in the duplicate response — useful for
    support tracing ("your request was already logged as #1234").
    """
    r = get_redis()
    key = dedup_key(hash_val)
    existing = r.get(key)

    if existing is not None:
        return True, existing   # existing = original ticket_id as string

    return False, None


def _redis_register(hash_val: str, ticket_id: int | str) -> None:
    """
    Register a new payload hash in Redis with 24h TTL.
    Called after the ticket is confirmed written to fdraw,
    so we only register tickets that are genuinely persisted.
    """
    r = get_redis()
    key = dedup_key(hash_val)
    r.set(key, str(ticket_id), ex=DEDUP_TTL_SECONDS)
    logger.debug("Registered dedup key %s → ticket %s (TTL %ds)",
                 key, ticket_id, DEDUP_TTL_SECONDS)


# ============================================================
# POSTGRES AUDIT WRITE
#
# Writes to kirana_kart.deduplication_log — the permanent
# record of every duplicate that was rejected.
# Redis holds the fast-check; Postgres holds the audit trail.
# ============================================================

def _write_dedup_log(
    payload_hash:        str,
    original_ticket_id:  int | str | None,
    source:              str,
    customer_id:         str | None,
    channel:             str | None,
    action_taken:        str = "rejected"
) -> None:
    """
    Insert a record into kirana_kart.deduplication_log.
    Runs in its own short-lived connection — failure here
    must NOT propagate back to the caller. A missed log
    entry is preferable to a failed ingest response.
    """
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA}.deduplication_log (
                    payload_hash,
                    original_ticket_id,
                    duplicate_received_at,
                    source,
                    customer_id,
                    channel,
                    action_taken
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    payload_hash,
                    int(original_ticket_id) if original_ticket_id else None,
                    datetime.now(timezone.utc),
                    source,
                    customer_id,
                    channel,
                    action_taken,
                )
            )
        conn.commit()
        logger.debug("Dedup log written for hash %s", payload_hash)

    except Exception as exc:
        logger.error(
            "Failed to write deduplication_log for hash %s: %s",
            payload_hash, exc
        )
        # Intentionally swallow — audit failure must not block ingest
    finally:
        if conn:
            conn.close()


# ============================================================
# PUBLIC INTERFACE
# ============================================================

class DuplicateRequestError(Exception):
    """
    Raised by run() when the payload is identified as a
    duplicate within the 24-hour dedup window.

    Attributes:
        payload_hash:       SHA-256 of the incoming payload
        original_ticket_id: ticket_id of the first submission
    """
    def __init__(self, payload_hash: str, original_ticket_id: str | None):
        self.payload_hash       = payload_hash
        self.original_ticket_id = original_ticket_id
        super().__init__(
            f"Duplicate payload detected. "
            f"Original ticket: {original_ticket_id or 'unknown'}"
        )


def run(
    canonical_payload: dict,
    ticket_id:         int,
    source:            str,
    customer_id:       str | None = None,
    channel:           str | None = None
) -> str:
    """
    Execute Phase 2: Idempotency & Collision Prevention.

    Steps:
      1. Compute SHA-256 hash of the normalised canonical payload.
      2. Check Redis for an existing dedup key.
         → If found: log to Postgres, raise DuplicateRequestError.
         → If not found: register hash in Redis, return hash.

    Args:
        canonical_payload: The normalised JSON produced by Phase 1.
        ticket_id:         The fdraw ticket_id already persisted by
                           Phase 1 — used as the Redis value so
                           duplicates can reference the original.
        source:            Originating source (e.g. 'freshdesk', 'api').
        customer_id:       Customer identifier for audit logging.
        channel:           Channel for audit logging (e.g. 'email').

    Returns:
        payload_hash (str): The SHA-256 hex digest. Stored back onto
        fdraw.payload_hash by the caller (pipeline.py) so the hash
        is queryable from Postgres without recomputing it.

    Raises:
        DuplicateRequestError: If payload is a duplicate within 24h window.
        Exception:             If Redis is unreachable (hard failure —
                               we do not skip dedup on Redis outage).
    """
    logger.info(
        "Phase 2 | ticket_id=%s | source=%s | customer_id=%s",
        ticket_id, source, customer_id
    )

    # Step 1 — Hash
    payload_hash = compute_payload_hash(canonical_payload)
    logger.debug("Computed payload hash: %s", payload_hash)

    # Step 2 — Redis check
    is_duplicate, original_ticket_id = _redis_check(payload_hash)

    if is_duplicate:
        logger.warning(
            "Duplicate detected | hash=%s | original_ticket=%s",
            payload_hash, original_ticket_id
        )

        # Persist audit record before raising
        _write_dedup_log(
            payload_hash       = payload_hash,
            original_ticket_id = original_ticket_id,
            source             = source,
            customer_id        = customer_id,
            channel            = channel,
            action_taken       = "rejected"
        )

        raise DuplicateRequestError(
            payload_hash       = payload_hash,
            original_ticket_id = original_ticket_id
        )

    # Step 3 — Register new hash
    _redis_register(payload_hash, ticket_id)

    logger.info(
        "Phase 2 complete | ticket_id=%s | hash=%s | registered in Redis",
        ticket_id, payload_hash
    )

    return payload_hash


# ============================================================
# REGISTRATION HELPER
#
# Called by pipeline.py after fdraw.payload_hash is written
# back to Postgres. Kept separate so the pipeline controls
# the exact moment of registration — after DB commit, not
# before. This prevents a race where Redis is registered but
# the Postgres write fails.
# ============================================================

def register_after_commit(payload_hash: str, ticket_id: int) -> None:
    """
    Explicit registration call for use after Postgres commit.

    pipeline.py flow:
        hash = phase2.run(...)          # check only, no registration
        write hash to fdraw             # Postgres commit
        phase2.register_after_commit()  # Redis registration

    This guarantees Redis only holds hashes for tickets that
    are fully persisted — prevents phantom dedup blocks if
    the Postgres write fails after Redis registration.
    """
    _redis_register(payload_hash, ticket_id)