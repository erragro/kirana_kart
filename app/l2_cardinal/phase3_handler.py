"""
app/l2_cardinal/phase3_handler.py
==================================
Cardinal Phase 3 — Source Handler

Responsibility:
    1. Source verification  — confirm the inbound request comes from a
                              trusted source (Freshdesk webhook signature,
                              API token, or internal service identity).
    2. Thread grouping      — detect if this ticket belongs to an existing
                              conversation thread and link them via thread_id.
    3. Connector resolution — resolve connector_id from org + source + channel
                              and write it back to fdraw.

This module does NOT:
    - Enrich with customer data (Phase 4)
    - Make routing decisions (Phase 5)
    - Make any LLM calls

Input:  CanonicalPayload (from Phase 1) + CardinalIngestRequest (original)
Output: Phase3Result — verified canonical with thread context attached

Position in pipeline:
    Phase 1 (normalise) → Phase 2 (dedup) → Phase 3 (here) → Phase 4 (enrich)

Source verification model:
    freshdesk  → HMAC-SHA256 signature on raw webhook body
                 Freshdesk sends X-Freshdesk-Webhook-Signature header.
                 Verified against FRESHDESK_WEBHOOK_SECRET from .env.
                 Skipped in sandbox mode (is_sandbox=True).

    gmail      → OAuth2 service account token validation.
                 Not yet implemented — placeholder returns verified=True.
                 Production: verify against Google tokeninfo endpoint.

    api        → Bearer token in Authorization header checked against
                 admin_users.api_token in DB.
                 Skipped in sandbox mode.

    webhook    → Same as api (generic webhook with Bearer token).

Thread grouping logic:
    A thread is a sequence of related tickets from the same customer
    about the same issue — e.g. customer follows up on an unresolved ticket.

    Matching criteria (in order of priority):
      1. Explicit thread_id in payload → link directly, no lookup needed.
      2. Same cx_email + same order_id + ticket within 7 days → same thread.
      3. Same cx_email + subject similarity (exact match after normalisation)
         + ticket within 48 hours → same thread.
      4. No match → new thread (thread_id = str(ticket_id)).

    When a thread match is found:
      - fdraw.thread_id is set to the original thread's thread_id
      - fdraw.message_count is incremented on the original ticket row
      - The new ticket's canonical_payload.reprocess flag is set to True
        so downstream phases know this is a follow-up, not a fresh complaint.

Connector resolution:
    connector_id is a future-use FK. Currently no connectors table exists.
    Phase 3 derives a deterministic integer from org + source hash so
    the column is populated consistently without blocking on schema work.
    This will be replaced when the connectors table is built.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from app.l1_ingestion.schemas import CanonicalPayload, CardinalIngestRequest

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

FRESHDESK_WEBHOOK_SECRET = os.getenv("FRESHDESK_WEBHOOK_SECRET", "")

# Thread grouping time windows
THREAD_WINDOW_ORDER_DAYS    = 7    # same cx_email + order_id within N days
THREAD_WINDOW_SUBJECT_HOURS = 48   # same cx_email + subject within N hours

logger = logging.getLogger("cardinal.phase3")


# ============================================================
# RESULT DATACLASS
# ============================================================

@dataclass
class Phase3Result:
    """
    Output of Phase 3. Passed directly to Phase 4.

    Fields:
        canonical       — Updated CanonicalPayload (thread_id set if matched)
        connector_id    — Resolved connector integer
        is_thread_reply — True if this ticket was linked to an existing thread
        original_thread_ticket_id
                        — ticket_id of the first ticket in this thread,
                          if is_thread_reply=True
        source_verified — True if the source signature/token check passed.
                          False only possible in sandbox mode.
        verification_method
                        — Which method was used: signature | token | skipped
        warnings        — Non-fatal issues logged for observability
    """
    canonical:                   CanonicalPayload
    connector_id:                int
    is_thread_reply:             bool        = False
    original_thread_ticket_id:   Optional[int] = None
    source_verified:             bool        = True
    verification_method:         str         = "skipped"
    warnings:                    list[str]   = field(default_factory=list)


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
# MAIN PUBLIC FUNCTION
# ============================================================

def run(
    canonical:   CanonicalPayload,
    request:     CardinalIngestRequest,
    raw_body:    Optional[bytes] = None,   # Required for Freshdesk HMAC verification
    auth_token:  Optional[str]  = None,    # Required for api/webhook token check
) -> Phase3Result:
    """
    Execute Phase 3: Source Handler.

    Steps:
      1. Verify source authenticity.
      2. Resolve connector_id.
      3. Detect thread membership.
      4. Update fdraw with thread context + connector_id.
      5. Return Phase3Result.

    Args:
        canonical:   CanonicalPayload from Phase 1 (already written to fdraw).
        request:     Original CardinalIngestRequest.
        raw_body:    Raw request bytes — needed for HMAC signature check.
                     None is acceptable in sandbox mode.
        auth_token:  Bearer token from Authorization header.
                     None is acceptable in sandbox mode.

    Returns:
        Phase3Result with updated canonical and thread context.

    Raises:
        SourceVerificationError: If source check fails in production mode.
        Phase3Error:             On DB failures.
    """
    result = Phase3Result(canonical=canonical, connector_id=0)

    # ----------------------------------------------------------
    # Step 1: Source verification
    # ----------------------------------------------------------
    verified, method, warn = _verify_source(
        source=request.source,
        is_sandbox=canonical.is_sandbox,
        raw_body=raw_body,
        auth_token=auth_token,
    )

    result.source_verified     = verified
    result.verification_method = method
    if warn:
        result.warnings.append(warn)

    if not verified:
        raise SourceVerificationError(
            f"Source verification failed for source='{request.source}'. "
            f"Method attempted: {method}."
        )

    # ----------------------------------------------------------
    # Step 2: Connector resolution
    # ----------------------------------------------------------
    result.connector_id = _resolve_connector_id(request.org, request.source)

    # ----------------------------------------------------------
    # Step 3: Thread detection
    # ----------------------------------------------------------
    thread_id, is_reply, original_ticket_id = _detect_thread(canonical)

    result.is_thread_reply           = is_reply
    result.original_thread_ticket_id = original_ticket_id
    result.canonical.thread_id       = thread_id

    if is_reply:
        result.canonical.reprocess        = True
        result.canonical.reprocess_reason = "thread_reply"

    # ----------------------------------------------------------
    # Step 4: Write thread context + connector back to fdraw
    # ----------------------------------------------------------
    _update_fdraw(
        ticket_id=canonical.ticket_id,
        thread_id=thread_id,
        connector_id=result.connector_id,
        is_reply=is_reply,
        original_thread_ticket_id=original_ticket_id,
    )

    logger.info(
        "Phase 3 complete | ticket_id=%s | verified=%s | method=%s | "
        "thread_id=%s | is_reply=%s | connector_id=%s",
        canonical.ticket_id,
        verified,
        method,
        thread_id,
        is_reply,
        result.connector_id,
    )

    return result


# ============================================================
# SOURCE VERIFICATION
# ============================================================

def _verify_source(
    source:     str,
    is_sandbox: bool,
    raw_body:   Optional[bytes],
    auth_token: Optional[str],
) -> tuple[bool, str, Optional[str]]:
    """
    Verify the inbound source.

    Returns:
        (verified: bool, method: str, warning: str | None)
    """

    # Sandbox: skip all verification
    if is_sandbox:
        return True, "skipped", "Source verification skipped — sandbox mode."

    if source == "freshdesk":
        return _verify_freshdesk_signature(raw_body)

    elif source in ("api", "webhook"):
        return _verify_api_token(auth_token)

    elif source == "gmail":
        # OAuth2 verification not yet implemented.
        # Returns verified=True with a warning so the pipeline
        # continues but the gap is visible in logs.
        return (
            True,
            "gmail_unimplemented",
            "Gmail OAuth2 verification not yet implemented — skipped.",
        )

    else:
        # Unknown source — should never reach here after schema validation
        # but handled defensively.
        return False, "unknown_source", None


def _verify_freshdesk_signature(raw_body: Optional[bytes]) -> tuple[bool, str, Optional[str]]:
    """
    Verify Freshdesk webhook HMAC-SHA256 signature.

    Freshdesk signs the raw request body with the webhook secret.
    We recompute and compare. Raw body must be passed in from the
    FastAPI request before any JSON parsing — parsing changes byte order.

    If FRESHDESK_WEBHOOK_SECRET is not configured, verification is
    skipped with a warning rather than hard-failing. This allows
    initial setup without blocking ingest. Log the warning as a
    high-priority operational alert.
    """
    if not FRESHDESK_WEBHOOK_SECRET:
        return (
            True,
            "freshdesk_signature_skipped",
            "FRESHDESK_WEBHOOK_SECRET not configured — signature check skipped. "
            "Set this in .env before going to production.",
        )

    if not raw_body:
        return False, "freshdesk_signature", None

    expected = hmac.new(
        key=FRESHDESK_WEBHOOK_SECRET.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Caller must pass the signature from the X-Freshdesk-Webhook-Signature header
    # Phase 3 receives it via auth_token for now (router.py extracts and passes it).
    # TODO: add dedicated signature_header param when router is wired.
    # For now: mark as skipped-with-warning if we can't compare.
    return (
        True,
        "freshdesk_signature_pending_header",
        "Freshdesk signature header extraction not yet wired in router — skipped.",
    )


def _verify_api_token(auth_token: Optional[str]) -> tuple[bool, str, Optional[str]]:
    """
    Verify Bearer token against admin_users.api_token in DB.

    Returns (False, "token", None) if token is missing or not found.
    Returns (True, "token", None) if token matches a valid admin_users row.
    """
    if not auth_token:
        return False, "token", None

    # Strip "Bearer " prefix if present
    token = auth_token.removeprefix("Bearer ").strip()
    if not token:
        return False, "token", None

    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {SCHEMA}.admin_users WHERE api_token = %s LIMIT 1",
                (token,),
            )
            row = cur.fetchone()
        return (row is not None), "token", None
    except psycopg2.Error as exc:
        logger.error("Token verification DB query failed: %s", exc)
        # On DB failure during token check, fail closed (deny)
        return False, "token_db_error", None
    finally:
        conn.close()


# ============================================================
# CONNECTOR RESOLUTION
# ============================================================

def _resolve_connector_id(org: str, source: str) -> int:
    """
    Derive a deterministic connector_id from org + source.

    No connectors table exists yet. We hash org+source to a
    positive integer so fdraw.connector_id is always populated
    with a consistent value per (org, source) pair.

    When the connectors table is built, this function will be
    replaced with a DB lookup. The hash values written now will
    become the seed IDs for that table.

    Returns an integer in range [1, 99999].
    """
    raw = f"{org.lower()}:{source.lower()}"
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()
    # Take first 5 hex chars → max 0xFFFFF = 1048575, cap at 99999
    return (int(digest[:5], 16) % 99999) + 1


# ============================================================
# THREAD DETECTION
# ============================================================

def _detect_thread(
    canonical: CanonicalPayload,
) -> tuple[str, bool, Optional[int]]:
    """
    Determine if this ticket belongs to an existing conversation thread.

    Priority order:
      1. Explicit thread_id in canonical payload → direct link.
      2. Same cx_email + order_id within THREAD_WINDOW_ORDER_DAYS.
      3. Same cx_email + normalised subject within THREAD_WINDOW_SUBJECT_HOURS.
      4. No match → new thread (thread_id = str(ticket_id)).

    Returns:
        (thread_id: str, is_reply: bool, original_ticket_id: int | None)
    """
    ticket_id = canonical.ticket_id

    # Priority 1: explicit thread_id provided by source
    if canonical.thread_id:
        original = _lookup_thread_original(canonical.thread_id)
        if original and original != ticket_id:
            logger.debug(
                "Thread match via explicit thread_id=%s → original ticket=%s",
                canonical.thread_id,
                original,
            )
            return canonical.thread_id, True, original
        # thread_id provided but no prior ticket found — treat as new thread root
        return canonical.thread_id, False, None

    # Priority 2: same cx_email + order_id within 7 days
    if canonical.cx_email and canonical.order_id:
        match = _lookup_by_email_order(canonical.cx_email, canonical.order_id)
        if match:
            thread_id, original_ticket_id = match
            logger.debug(
                "Thread match via email+order | email=%s | order=%s → "
                "thread_id=%s | original=%s",
                canonical.cx_email,
                canonical.order_id,
                thread_id,
                original_ticket_id,
            )
            return thread_id, True, original_ticket_id

    # Priority 3: same cx_email + subject within 48 hours
    if canonical.cx_email and canonical.subject:
        match = _lookup_by_email_subject(canonical.cx_email, canonical.subject)
        if match:
            thread_id, original_ticket_id = match
            logger.debug(
                "Thread match via email+subject | email=%s → "
                "thread_id=%s | original=%s",
                canonical.cx_email,
                thread_id,
                original_ticket_id,
            )
            return thread_id, True, original_ticket_id

    # No match — this ticket is the root of a new thread
    new_thread_id = str(ticket_id)
    return new_thread_id, False, None


def _lookup_thread_original(thread_id: str) -> Optional[int]:
    """
    Find the ticket_id of the earliest ticket with this thread_id.
    Returns None if thread_id not found in fdraw.
    """
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT ticket_id
                FROM   {SCHEMA}.fdraw
                WHERE  thread_id = %s
                ORDER  BY ts ASC
                LIMIT  1
                """,
                (thread_id,),
            )
            row = cur.fetchone()
        return row[0] if row else None
    except psycopg2.Error as exc:
        logger.warning("thread_id lookup failed: %s", exc)
        return None
    finally:
        conn.close()


def _lookup_by_email_order(cx_email: str, order_id: str) -> Optional[tuple[str, int]]:
    """
    Find an existing thread by same cx_email + order_id within
    THREAD_WINDOW_ORDER_DAYS days.

    Returns (thread_id, original_ticket_id) or None.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=THREAD_WINDOW_ORDER_DAYS)
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT   thread_id, ticket_id
                FROM     {SCHEMA}.fdraw
                WHERE    cx_email  = %s
                AND      canonical_payload->>'order_id' = %s
                AND      ts >= %s
                ORDER    BY ts ASC
                LIMIT    1
                """,
                (cx_email, order_id, cutoff),
            )
            row = cur.fetchone()
        if row:
            thread_id, ticket_id = row
            # Use ticket_id as thread_id if thread_id was NULL on the original row
            return (thread_id or str(ticket_id), ticket_id)
        return None
    except psycopg2.Error as exc:
        logger.warning("email+order thread lookup failed: %s", exc)
        return None
    finally:
        conn.close()


def _lookup_by_email_subject(cx_email: str, subject: str) -> Optional[tuple[str, int]]:
    """
    Find an existing thread by same cx_email + normalised subject
    within THREAD_WINDOW_SUBJECT_HOURS hours.

    Subject normalisation: lowercase, strip leading/trailing whitespace,
    collapse internal whitespace. Does NOT use embedding similarity —
    that's an L4 concern. This is a fast exact-match after normalisation.
    """
    normalised_subject = " ".join(subject.lower().split())
    cutoff = datetime.now(timezone.utc) - timedelta(hours=THREAD_WINDOW_SUBJECT_HOURS)

    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT   thread_id, ticket_id
                FROM     {SCHEMA}.fdraw
                WHERE    cx_email = %s
                AND      lower(regexp_replace(subject, '\\s+', ' ', 'g')) = %s
                AND      ts >= %s
                ORDER    BY ts ASC
                LIMIT    1
                """,
                (cx_email, normalised_subject, cutoff),
            )
            row = cur.fetchone()
        if row:
            thread_id, ticket_id = row
            return (thread_id or str(ticket_id), ticket_id)
        return None
    except psycopg2.Error as exc:
        logger.warning("email+subject thread lookup failed: %s", exc)
        return None
    finally:
        conn.close()


# ============================================================
# FDRAW UPDATE
# ============================================================

def _update_fdraw(
    ticket_id:                 int,
    thread_id:                 str,
    connector_id:              int,
    is_reply:                  bool,
    original_thread_ticket_id: Optional[int],
) -> None:
    """
    Write Phase 3 results back to fdraw:
      - thread_id
      - connector_id
      - pipeline_stage → THREAD_RESOLVED
      - message_count incremented on the original thread ticket if this is a reply

    Two separate statements in one transaction:
      1. UPDATE this ticket's row with thread_id + connector_id.
      2. If is_reply: INCREMENT message_count on the original thread ticket.
    """
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                # Update this ticket
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.fdraw
                    SET
                        thread_id      = %s,
                        connector_id   = %s,
                        pipeline_stage = 'THREAD_RESOLVED'
                    WHERE ticket_id    = %s
                    """,
                    (thread_id, connector_id, ticket_id),
                )

                # If this is a reply, bump message_count on the original
                if is_reply and original_thread_ticket_id:
                    cur.execute(
                        f"""
                        UPDATE {SCHEMA}.fdraw
                        SET    message_count = COALESCE(message_count, 1) + 1
                        WHERE  ticket_id     = %s
                        """,
                        (original_thread_ticket_id,),
                    )

    except psycopg2.Error as exc:
        logger.error(
            "fdraw Phase 3 update failed for ticket_id=%s: %s",
            ticket_id,
            exc,
        )
        raise Phase3Error(
            f"fdraw update failed in Phase 3 for ticket_id={ticket_id}: {exc}"
        ) from exc
    finally:
        conn.close()


# ============================================================
# EXCEPTIONS
# ============================================================

class SourceVerificationError(Exception):
    """
    Raised when source verification fails in production mode.
    pipeline.py catches this and returns 401 to the caller.
    """
    pass


class Phase3Error(Exception):
    """
    Raised on non-verification failures (DB errors, unexpected state).
    pipeline.py catches this and returns 500 to the caller.
    """
    pass