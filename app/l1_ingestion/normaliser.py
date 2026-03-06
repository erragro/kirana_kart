"""
app/l1_ingestion/normaliser.py
==============================
Cardinal Phase 1 — Ingestion & Normalisation

Responsibility:
    Convert a validated CardinalIngestRequest into:
      1. A CanonicalPayload (standardised in-memory representation)
      2. A persisted row in kirana_kart.fdraw

This module does NOT:
    - Run deduplication (Phase 2)
    - Enrich with customer data (Phase 4)
    - Make any LLM calls
    - Validate business rules

Input:  CardinalIngestRequest (already Pydantic-validated by the router)
Output: CanonicalPayload + fdraw.ticket_id

DB contract:
    Writes one row to fdraw.
    ticket_id is returned to the caller (pipeline.py) for use in all
    downstream phases.
    canonical_payload (jsonb) is written with payload_hash=None at this
    stage. Phase 2 fills in payload_hash via a separate UPDATE after
    the dedup check passes.

Source routing:
    source=freshdesk  →  parse via FreshDeskPayload sub-schema
    source=gmail      →  parse via DirectAPIPayload sub-schema (same shape)
    source=api        →  parse via DirectAPIPayload sub-schema
    source=webhook    →  parse via DirectAPIPayload sub-schema

group_id:
    fdraw.group_id is NOT NULL. If the source doesn't provide one,
    we derive a fallback: "{bl_slug}_{mod_slug}_{org_slug}".
    This keeps the constraint satisfied without silently inventing data.

ticket_id allocation:
    For Freshdesk payloads, ticket_id comes from the source system.
    For all other sources, sl and ticket_id are set to the same sequence
    value in a single INSERT:
        - ticket_id = nextval('kirana_kart.fdraw_sl_seq')  → advances sequence
        - sl        = currval('kirana_kart.fdraw_sl_seq')  → reads same value
    This guarantees sl == ticket_id without a CTE or two-step approach,
    and satisfies the NOT NULL constraint on both columns atomically.

FIX (2026-03-06 v1):
    _insert_direct_row previously used a CTE (INSERT + UPDATE) to set
    ticket_id = sl. Postgres checks NOT NULL during the INSERT phase of
    the CTE before the UPDATE runs — constraint violated.

FIX (2026-03-06 v2):
    Switched to nextval() for ticket_id and sl DEFAULT — but sl's DEFAULT
    also calls nextval(), advancing the sequence a second time. Result:
    sl = ticket_id + 1. RETURNING sl returned the wrong value.

FIX (2026-03-06 v3 — current):
    Both sl and ticket_id are set explicitly in the INSERT:
        ticket_id = nextval(seq)   — advances sequence once, gets new value
        sl        = currval(seq)   — reads current value, no advance
    RETURNING ticket_id (not sl) — returns the value we care about.
    Sequence advances exactly once. sl == ticket_id. NOT NULL satisfied.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from app.l1_ingestion.schemas import (
    CanonicalPayload,
    CardinalIngestRequest,
    DirectAPIPayload,
    FreshDeskPayload,
)

# ============================================================
# ENVIRONMENT
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = os.getenv("DB_PORT", "5432")
DB_NAME     = os.getenv("DB_NAME", "orgintelligence")
DB_USER     = os.getenv("DB_USER", "orguser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
SCHEMA      = "kirana_kart"

logger = logging.getLogger(__name__)


# ============================================================
# DB CONNECTION
# ============================================================

def _get_connection() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


# ============================================================
# SOURCE-SPECIFIC PARSERS
# Each parser extracts the canonical fields from the raw
# payload dict according to the source system's schema.
# Returns a flat dict that maps 1:1 to CanonicalPayload fields.
# ============================================================

def _parse_freshdesk(raw: dict) -> dict:
    """
    Parse a Freshdesk webhook payload.
    Validates via FreshDeskPayload sub-schema first, then extracts.
    """
    parsed = FreshDeskPayload(**raw)
    return {
        "ticket_id":         parsed.ticket_id,
        "group_id":          parsed.group_id,
        "group_name":        parsed.group_name,
        "cx_email":          str(parsed.cx_email) if parsed.cx_email else None,
        "customer_id":       parsed.customer_id,
        "order_id":          parsed.order_id,
        "subject":           parsed.subject,
        "description":       parsed.description,
        "thread_id":         parsed.thread_id,
        "tags":              parsed.tags,
        "img_flg":           parsed.img_flg or 0,
        "attachment":        parsed.attachment or 0,
        "source_created_at": parsed.created_at,
        "source_updated_at": parsed.updated_at,
    }


def _parse_direct(raw: dict) -> dict:
    """
    Parse a direct API / Gmail / webhook payload.
    Validates via DirectAPIPayload sub-schema first, then extracts.

    NOTE: cx_email is handled as str (not EmailStr) in DirectAPIPayload
    to avoid email-validator rejecting reserved TLDs like .test used
    in sandbox and test environments.
    """
    parsed = DirectAPIPayload(**raw)
    return {
        "ticket_id":         None,            # Not provided by direct callers
        "group_id":          None,            # Will be derived as fallback below
        "group_name":        None,
        "cx_email":          str(parsed.cx_email) if parsed.cx_email else None,
        "customer_id":       parsed.customer_id,
        "order_id":          parsed.order_id,
        "subject":           parsed.subject,
        "description":       parsed.description,
        "thread_id":         parsed.thread_id,
        "tags":              None,
        "img_flg":           1 if parsed.attachment_urls else 0,
        "attachment":        len(parsed.attachment_urls) if parsed.attachment_urls else 0,
        "source_created_at": None,
        "source_updated_at": None,
    }


# Registry: source → parser function
_SOURCE_PARSERS = {
    "freshdesk": _parse_freshdesk,
    "gmail":     _parse_direct,
    "api":       _parse_direct,
    "webhook":   _parse_direct,
}


# ============================================================
# NORMALISER — MAIN PUBLIC FUNCTION
# ============================================================

def run(request: CardinalIngestRequest) -> tuple[CanonicalPayload, int]:
    """
    Execute Phase 1: Ingestion & Normalisation.

    Steps:
      1. Route payload to the correct source parser.
      2. Build CanonicalPayload from parsed fields + request envelope.
      3. Derive fallback group_id if not present.
      4. Write one row to fdraw.
      5. Return (CanonicalPayload, ticket_id).

    Args:
        request:  Validated CardinalIngestRequest from the router.

    Returns:
        canonical:  The CanonicalPayload object. payload_hash is None
                    at this stage — Phase 2 fills it in.
        ticket_id:  The fdraw.ticket_id for this row.

    Raises:
        NormaliserError:  If DB write fails or payload cannot be parsed.
    """

    # ----------------------------------------------------------
    # Step 1: Parse source payload
    # ----------------------------------------------------------
    parser = _SOURCE_PARSERS.get(request.source)
    if not parser:
        raise NormaliserError(
            f"No parser registered for source '{request.source}'"
        )

    try:
        parsed_fields = parser(request.payload)
    except Exception as exc:
        raise NormaliserError(
            f"Payload parsing failed for source '{request.source}': {exc}"
        ) from exc

    # ----------------------------------------------------------
    # Step 2: Derive group_id fallback
    # fdraw.group_id is NOT NULL — must always have a value.
    # Freshdesk provides it; other sources don't.
    # ----------------------------------------------------------
    group_id = parsed_fields.get("group_id") or _derive_group_id(request)

    # ----------------------------------------------------------
    # Step 3: Build CanonicalPayload
    # ----------------------------------------------------------
    meta = request.metadata

    canonical = CanonicalPayload(
        # Envelope fields
        org=request.org,
        channel=request.channel,
        source=request.source,
        business_line=request.business_line,
        module=request.module,
        is_sandbox=meta.test_mode if meta else False,

        # Ticket fields from parsed source
        ticket_id=parsed_fields.get("ticket_id"),
        group_id=group_id,
        group_name=parsed_fields.get("group_name"),
        cx_email=parsed_fields.get("cx_email"),
        customer_id=parsed_fields.get("customer_id"),
        order_id=parsed_fields.get("order_id"),
        subject=parsed_fields.get("subject"),
        description=parsed_fields.get("description"),
        thread_id=parsed_fields.get("thread_id"),
        tags=parsed_fields.get("tags"),
        img_flg=parsed_fields.get("img_flg", 0),
        attachment=parsed_fields.get("attachment", 0),
        source_created_at=parsed_fields.get("source_created_at"),
        source_updated_at=parsed_fields.get("source_updated_at"),

        # Metadata fields
        environment=meta.environment if meta else "production",
        called_by=meta.called_by if meta else None,
        agent_id=meta.agent_id if meta else None,
        reprocess=meta.reprocess if meta else False,
        reprocess_reason=meta.reprocess_reason if meta else None,

        # Raw payload preserved for audit
        raw_payload=request.payload,

        # Hash is None here — Phase 2 fills it in
        payload_hash=None,
    )

    # ----------------------------------------------------------
    # Step 4: Write to fdraw
    # ----------------------------------------------------------
    ticket_id = _write_to_fdraw(canonical, request)

    # ----------------------------------------------------------
    # Step 5: Update canonical with the final ticket_id
    # (may differ from source ticket_id for non-Freshdesk sources)
    # ----------------------------------------------------------
    canonical.ticket_id = ticket_id

    logger.info(
        "Phase 1 complete | ticket_id=%s | org=%s | channel=%s | module=%s | sandbox=%s",
        ticket_id,
        request.org,
        request.channel,
        request.module,
        canonical.is_sandbox,
    )

    return canonical, ticket_id


# ============================================================
# FDRAW WRITER
# ============================================================

def _write_to_fdraw(canonical: CanonicalPayload, request: CardinalIngestRequest) -> int:
    """
    Insert one row into fdraw and return the ticket_id.

    For Freshdesk payloads: ticket_id comes from the source.
      - We use ON CONFLICT (ticket_id) DO UPDATE to handle Freshdesk
        webhook retries gracefully — same ticket_id re-ingested just
        refreshes the row rather than failing or creating a duplicate.

    For non-Freshdesk payloads: ticket_id is NULL in canonical.
      - sl and ticket_id are both set from the same sequence value
        in a single INSERT statement. See _insert_direct_row.

    canonical_payload is written as JSONB. It will be updated
    by Phase 2 (adds payload_hash) and Phase 4 (adds enrichment fields).
    """

    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                if canonical.ticket_id is not None:
                    # Freshdesk path: source-provided ticket_id
                    ticket_id = _upsert_freshdesk_row(cur, canonical)
                else:
                    # Direct / API path: generate ticket_id from sequence
                    ticket_id = _insert_direct_row(cur, canonical)

        return ticket_id

    except psycopg2.Error as exc:
        logger.error("fdraw write failed: %s", exc)
        raise NormaliserError(f"Database write to fdraw failed: {exc}") from exc
    finally:
        conn.close()


def _upsert_freshdesk_row(cur, canonical: CanonicalPayload) -> int:
    """
    INSERT ... ON CONFLICT (ticket_id) DO UPDATE for Freshdesk source.
    Handles webhook retries and Freshdesk re-deliveries safely.
    """
    cur.execute(
        f"""
        INSERT INTO {SCHEMA}.fdraw (
            ticket_id,
            group_id,
            group_name,
            cx_email,
            status,
            subject,
            description,
            tags,
            img_flg,
            attachment,
            created_at,
            updated_at,
            pipeline_stage,
            source,
            thread_id,
            module,
            canonical_payload,
            processed
        )
        VALUES (
            %(ticket_id)s,
            %(group_id)s,
            %(group_name)s,
            %(cx_email)s,
            0,
            %(subject)s,
            %(description)s,
            %(tags)s,
            %(img_flg)s,
            %(attachment)s,
            %(source_created_at)s,
            %(source_updated_at)s,
            'INGESTED',
            %(source)s,
            %(thread_id)s,
            %(module)s,
            %(canonical_payload)s,
            0
        )
        ON CONFLICT (ticket_id) DO UPDATE SET
            updated_at        = EXCLUDED.updated_at,
            pipeline_stage    = 'REINGESTED',
            canonical_payload = EXCLUDED.canonical_payload,
            processed         = 0
        RETURNING ticket_id
        """,
        {
            "ticket_id":         canonical.ticket_id,
            "group_id":          canonical.group_id,
            "group_name":        canonical.group_name,
            "cx_email":          canonical.cx_email,
            "subject":           canonical.subject,
            "description":       canonical.description,
            "tags":              canonical.tags,
            "img_flg":           canonical.img_flg,
            "attachment":        canonical.attachment,
            "source_created_at": canonical.source_created_at,
            "source_updated_at": canonical.source_updated_at,
            "source":            canonical.source,
            "thread_id":         canonical.thread_id,
            "module":            canonical.module,
            "canonical_payload": psycopg2.extras.Json(
                canonical.model_dump(exclude={"raw_payload"})
            ),
        },
    )
    row = cur.fetchone()
    return row[0]


def _insert_direct_row(cur, canonical: CanonicalPayload) -> int:
    """
    INSERT for non-Freshdesk sources (api, gmail, webhook).

    sl and ticket_id must be equal and both NOT NULL.
    The sequence is advanced exactly once:

        ticket_id = nextval(seq)  → advances sequence, gets new value N
        sl        = currval(seq)  → reads N without advancing again

    Both columns get the same value N in a single INSERT statement.
    RETURNING ticket_id gives us the value to hand back to the pipeline.

    Why not omit sl and rely on DEFAULT:
        sl DEFAULT also calls nextval() — that would advance the sequence
        a second time, making sl = N+1 and ticket_id = N. They would
        differ by 1 on every row.
    """
    cur.execute(
        f"""
        INSERT INTO {SCHEMA}.fdraw (
            sl,
            ticket_id,
            group_id,
            group_name,
            cx_email,
            status,
            subject,
            description,
            tags,
            img_flg,
            attachment,
            pipeline_stage,
            source,
            thread_id,
            module,
            canonical_payload,
            processed
        )
        VALUES (
            nextval('kirana_kart.fdraw_sl_seq'),
            currval('kirana_kart.fdraw_sl_seq'),
            %(group_id)s,
            %(group_name)s,
            %(cx_email)s,
            0,
            %(subject)s,
            %(description)s,
            %(tags)s,
            %(img_flg)s,
            %(attachment)s,
            'INGESTED',
            %(source)s,
            %(thread_id)s,
            %(module)s,
            %(canonical_payload)s,
            0
        )
        RETURNING ticket_id
        """,
        {
            "group_id":          canonical.group_id,
            "group_name":        canonical.group_name,
            "cx_email":          canonical.cx_email,
            "subject":           canonical.subject,
            "description":       canonical.description,
            "tags":              canonical.tags,
            "img_flg":           canonical.img_flg,
            "attachment":        canonical.attachment,
            "source":            canonical.source,
            "thread_id":         canonical.thread_id,
            "module":            canonical.module,
            "canonical_payload": psycopg2.extras.Json(
                canonical.model_dump(exclude={"raw_payload"})
            ),
        },
    )
    row = cur.fetchone()
    return row[0]


# ============================================================
# HELPER — group_id DERIVATION
# ============================================================

def _derive_group_id(request: CardinalIngestRequest) -> str:
    """
    Derive a fallback group_id when the source doesn't provide one.

    Format: {bl_slug}_{mod_slug}_{org_slug}
    Example: ecommerce_delivery_AcmeCorp → 'eco_deli_acmecorp' (truncated to 25 chars)

    fdraw.group_id is varchar(25) NOT NULL.
    """
    org_slug = request.org.lower().replace(" ", "")[:8]
    bl_slug  = request.business_line[:3]
    mod_slug = request.module[:4]
    derived  = f"{bl_slug}_{mod_slug}_{org_slug}"
    return derived[:25]


# ============================================================
# PHASE 2 HANDOFF HELPER
# Called by pipeline.py after Phase 2 returns the payload_hash.
# Updates fdraw.canonical_payload with the hash in-place.
# ============================================================

def update_payload_hash(ticket_id: int, payload_hash: str) -> None:
    """
    Write payload_hash back into fdraw.canonical_payload after
    Phase 2 computes it.

    Also writes to fdraw.preprocessing_hash to keep both columns
    consistent — preprocessing_hash is used by the LLM pipeline
    to detect stale preprocessed text.

    Called by pipeline.py after phase2.register_after_commit().
    """
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.fdraw
                    SET
                        canonical_payload = jsonb_set(
                            COALESCE(canonical_payload, '{{}}'),
                            '{{payload_hash}}',
                            to_jsonb(%(hash)s::text)
                        ),
                        preprocessing_hash = %(hash)s
                    WHERE ticket_id = %(ticket_id)s
                    """,
                    {"hash": payload_hash, "ticket_id": ticket_id},
                )
    except psycopg2.Error as exc:
        # Non-fatal — log and continue. Phase 2 Redis registration
        # already succeeded; this is a best-effort enrichment.
        logger.warning(
            "payload_hash update to fdraw failed for ticket_id=%s: %s",
            ticket_id,
            exc,
        )
    finally:
        conn.close()


# ============================================================
# EXCEPTION
# ============================================================

class NormaliserError(Exception):
    """
    Raised when Phase 1 cannot complete normalisation.
    pipeline.py catches this and returns a 422 or 500 to the caller.
    """
    pass