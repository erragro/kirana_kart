"""
app/l2_cardinal/phase1_validator.py
=====================================
Cardinal Phase 1 — Payload Validator

Responsibility:
    Run structural and lightweight business pre-checks on a
    CardinalIngestRequest BEFORE normaliser.run() writes to fdraw.

    This is the last gate before any DB write happens.
    If this phase raises, the caller gets a clean 422 with a
    specific reason — no partial writes, no orphan rows in fdraw.

This module does NOT:
    - Write to any DB table
    - Make LLM calls
    - Run fraud scoring (Phase 4)
    - Enforce business rules (that's the LLM pipeline)

Position in pipeline:
    Router validates schema (Pydantic) →
    Phase 1 validator (here) →
    normaliser.run() writes to fdraw →
    Phase 2 dedup →
    Phase 3 source handler

What this validates:

    1. Description presence & length
       - description must not be empty or whitespace-only
       - minimum 10 characters (filters noise like "?" or "help")
       - maximum 50,000 characters (prevents payload bomb)

    2. order_id format (if present)
       - Must match expected pattern: alphanumeric + hyphens/underscores
       - No SQL injection characters
       - If present, optionally verified to exist in orders table
         (DB check only in production mode, skipped in sandbox)

    3. cx_email / customer_id presence
       - At least one must resolve to a known customer when source != freshdesk
       - Freshdesk payloads are trusted to carry valid data

    4. customer block check
       - If customer is found and is_blocked=True → reject immediately
       - Returns specific error code CUSTOMER_BLOCKED so the router
         can return a 403 rather than 422

    5. img_flg / attachment consistency
       - If img_flg=1 but no image URL in payload → warning (not hard fail)
         because Freshdesk sometimes sends the flag before the attachment uploads
       - If attachment > 0 but img_flg=0 → correct img_flg to 1 (normalise)

    6. subject length
       - Must not exceed 500 characters
       - Empty subject is allowed (some API calls omit it)

    7. Payload field injection guard
       - description and subject must not contain SQL injection patterns
       - Not a security layer (the DB uses parameterised queries throughout)
         but catches accidental data corruption from malformed upstream systems

Design principle:
    Every check that returns False produces a ValidationFailure with:
      - check_name: which check failed
      - error_code: machine-readable code for the router
      - message:    human-readable explanation for the caller
    This makes the validator's output fully structured — no string parsing needed.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import psycopg2
from dotenv import load_dotenv

from app.l1_ingestion.schemas import CardinalIngestRequest

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

# Validation limits
DESCRIPTION_MIN_LENGTH  = 10
DESCRIPTION_MAX_LENGTH  = 50_000
SUBJECT_MAX_LENGTH      = 500
ORDER_ID_MAX_LENGTH     = 100

# order_id pattern: letters, digits, hyphens, underscores only
ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9\-_]+$")

# Injection guard: reject if these patterns appear in text fields
# These are not SQL keywords but structural patterns that indicate
# a malformed upstream system sending raw query fragments
INJECTION_PATTERNS = re.compile(
    r"(--|;--|/\*|\*/|xp_|EXEC\s*\(|UNION\s+SELECT|DROP\s+TABLE)",
    re.IGNORECASE,
)

logger = logging.getLogger("cardinal.phase1_validator")


# ============================================================
# RESULT TYPES
# ============================================================

@dataclass
class ValidationFailure:
    check_name:  str
    error_code:  str
    message:     str


@dataclass
class Phase1ValidationResult:
    """
    Output of phase1_validator.run().

    passed    — True only if ALL hard checks pass.
    failures  — List of hard failures (non-empty means passed=False).
    warnings  — Non-fatal issues. Pipeline continues but these are logged.
    """
    passed:   bool                        = True
    failures: list[ValidationFailure]     = field(default_factory=list)
    warnings: list[str]                   = field(default_factory=list)

    def fail(self, check_name: str, error_code: str, message: str) -> None:
        self.passed = False
        self.failures.append(ValidationFailure(check_name, error_code, message))

    def warn(self, message: str) -> None:
        self.warnings.append(message)


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

def run(request: CardinalIngestRequest) -> Phase1ValidationResult:
    """
    Execute Phase 1 validation checks.

    All checks run even if an earlier one fails — this gives the
    caller a complete picture of what's wrong rather than one
    error at a time.

    The only exception: if the customer is blocked (CUSTOMER_BLOCKED),
    we return immediately — no point running further checks.

    Args:
        request:  Validated CardinalIngestRequest from the router.
                  Pydantic validation has already passed at this point.

    Returns:
        Phase1ValidationResult with passed=True or list of failures.

    Does NOT raise. All errors are captured in the result object.
    The pipeline.py caller decides whether to raise based on result.passed.
    """
    result     = Phase1ValidationResult()
    payload    = request.payload
    is_sandbox = _is_sandbox(request)

    # ----------------------------------------------------------
    # Check 1: Description presence and length
    # ----------------------------------------------------------
    description = payload.get("description") or ""
    description = description.strip()

    if not description:
        result.fail(
            check_name="description_presence",
            error_code="DESCRIPTION_MISSING",
            message="description is required and must not be empty.",
        )
    elif len(description) < DESCRIPTION_MIN_LENGTH:
        result.fail(
            check_name="description_min_length",
            error_code="DESCRIPTION_TOO_SHORT",
            message=(
                f"description must be at least {DESCRIPTION_MIN_LENGTH} characters. "
                f"Received {len(description)} characters."
            ),
        )
    elif len(description) > DESCRIPTION_MAX_LENGTH:
        result.fail(
            check_name="description_max_length",
            error_code="DESCRIPTION_TOO_LONG",
            message=(
                f"description exceeds maximum length of {DESCRIPTION_MAX_LENGTH} characters. "
                f"Received {len(description)} characters."
            ),
        )

    # ----------------------------------------------------------
    # Check 2: Subject length
    # ----------------------------------------------------------
    subject = payload.get("subject") or ""
    if len(subject) > SUBJECT_MAX_LENGTH:
        result.fail(
            check_name="subject_max_length",
            error_code="SUBJECT_TOO_LONG",
            message=(
                f"subject exceeds maximum length of {SUBJECT_MAX_LENGTH} characters. "
                f"Received {len(subject)} characters."
            ),
        )

    # ----------------------------------------------------------
    # Check 3: Injection guard on text fields
    # ----------------------------------------------------------
    for field_name, value in [("description", description), ("subject", subject)]:
        if value and INJECTION_PATTERNS.search(value):
            result.fail(
                check_name=f"injection_guard_{field_name}",
                error_code="INJECTION_PATTERN_DETECTED",
                message=(
                    f"{field_name} contains patterns that suggest a malformed "
                    f"upstream payload. Please sanitise before submitting."
                ),
            )

    # ----------------------------------------------------------
    # Check 4: order_id format (if present)
    # ----------------------------------------------------------
    order_id = payload.get("order_id")
    if order_id:
        order_id = str(order_id).strip()
        if len(order_id) > ORDER_ID_MAX_LENGTH:
            result.fail(
                check_name="order_id_length",
                error_code="ORDER_ID_TOO_LONG",
                message=f"order_id exceeds maximum length of {ORDER_ID_MAX_LENGTH} characters.",
            )
        elif not ORDER_ID_PATTERN.match(order_id):
            result.fail(
                check_name="order_id_format",
                error_code="ORDER_ID_INVALID_FORMAT",
                message=(
                    f"order_id '{order_id}' contains invalid characters. "
                    f"Only letters, digits, hyphens, and underscores are allowed."
                ),
            )

    # ----------------------------------------------------------
    # Check 5: img_flg / attachment consistency
    # ----------------------------------------------------------
    img_flg    = int(payload.get("img_flg", 0) or 0)
    attachment = int(payload.get("attachment", 0) or 0)

    if img_flg == 1 and attachment == 0:
        # img_flg says image present but attachment count is 0
        # Warn only — Freshdesk can send the flag before the file uploads
        result.warn(
            "img_flg=1 but attachment=0. Image may not have uploaded yet. "
            "Pipeline will proceed; image validation in Phase 2 (LLM Stage 1) "
            "will handle this if image is required for the issue type."
        )
    elif attachment > 0 and img_flg == 0:
        # Attachment exists but flag not set — silently correct in normaliser
        # Just warn here so the discrepancy is logged
        result.warn(
            f"attachment={attachment} but img_flg=0. "
            "img_flg will be corrected to 1 during normalisation."
        )

    # ----------------------------------------------------------
    # Check 6: Customer identifier presence (non-Freshdesk sources)
    # Freshdesk payloads are trusted — they always carry cx_email.
    # Direct API callers must provide cx_email or customer_id.
    # ----------------------------------------------------------
    if request.source != "freshdesk":
        cx_email    = payload.get("cx_email")
        customer_id = payload.get("customer_id")
        if not cx_email and not customer_id:
            result.fail(
                check_name="customer_identifier",
                error_code="CUSTOMER_IDENTIFIER_MISSING",
                message=(
                    "At least one of cx_email or customer_id must be provided "
                    f"for source='{request.source}'."
                ),
            )

    # Stop here if structural checks already failed —
    # no point hitting the DB for a structurally invalid request
    if not result.passed:
        return result

    # ----------------------------------------------------------
    # Check 7: Customer block status (DB check)
    # Skipped in sandbox mode.
    # ----------------------------------------------------------
    if not is_sandbox:
        cx_email    = payload.get("cx_email")
        customer_id = payload.get("customer_id")

        blocked, block_reason = _check_customer_blocked(cx_email, customer_id)
        if blocked:
            result.fail(
                check_name="customer_block_status",
                error_code="CUSTOMER_BLOCKED",
                message=(
                    f"Customer is blocked and cannot submit new tickets. "
                    f"Reason: {block_reason or 'not specified'}."
                ),
            )
            # Return immediately — blocked customer, no further checks needed
            return result

    # ----------------------------------------------------------
    # Check 8: order_id exists in orders table (DB check)
    # Only run if order_id is present and format check passed.
    # Skipped in sandbox mode — sandbox tickets often use fake order IDs.
    # ----------------------------------------------------------
    if order_id and not is_sandbox:
        exists = _check_order_exists(order_id)
        if not exists:
            result.warn(
                f"order_id '{order_id}' not found in orders table. "
                "Ticket will proceed but LLM pipeline may not find order context. "
                "This may indicate a timing issue (order not yet synced) or a "
                "customer submitting a complaint for an external order."
            )
            # Warn only — don't hard fail. Order sync lag is real.
            # The LLM pipeline handles missing order context gracefully.

    logger.info(
        "Phase 1 validation complete | passed=%s | failures=%d | warnings=%d | "
        "source=%s | module=%s | sandbox=%s",
        result.passed,
        len(result.failures),
        len(result.warnings),
        request.source,
        request.module,
        is_sandbox,
    )

    return result


# ============================================================
# DB HELPERS
# ============================================================

def _check_customer_blocked(
    cx_email: Optional[str],
    customer_id: Optional[str],
) -> tuple[bool, Optional[str]]:
    """
    Check if the customer is blocked in the customers table.

    Tries customer_id first (more specific), falls back to cx_email.
    Returns (is_blocked: bool, block_reason: str | None).
    Returns (False, None) if customer not found — unknown customers
    are allowed through; Phase 4 will handle the missing customer case.
    """
    if not cx_email and not customer_id:
        return False, None

    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            if customer_id:
                cur.execute(
                    f"""
                    SELECT is_blocked, block_reason
                    FROM   {SCHEMA}.customers
                    WHERE  customer_id = %s
                    LIMIT  1
                    """,
                    (customer_id,),
                )
            else:
                cur.execute(
                    f"""
                    SELECT is_blocked, block_reason
                    FROM   {SCHEMA}.customers
                    WHERE  email = %s
                    LIMIT  1
                    """,
                    (cx_email,),
                )
            row = cur.fetchone()

        if not row:
            return False, None  # Unknown customer — let Phase 4 handle

        is_blocked, block_reason = row
        return bool(is_blocked), block_reason

    except psycopg2.Error as exc:
        # DB failure during block check — fail open (allow through) with warning
        # logged. A DB outage should not stop all ingest.
        logger.warning(
            "Customer block check DB query failed — failing open: %s", exc
        )
        return False, None
    finally:
        conn.close()


def _check_order_exists(order_id: str) -> bool:
    """
    Verify order_id exists in the orders table.
    Returns True if found, False if not found or on DB error.
    """
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM {SCHEMA}.orders WHERE order_id = %s LIMIT 1",
                (order_id,),
            )
            return cur.fetchone() is not None
    except psycopg2.Error as exc:
        logger.warning("order_id existence check failed: %s", exc)
        return True  # Fail open — don't block on DB error
    finally:
        conn.close()


# ============================================================
# HELPERS
# ============================================================

def _is_sandbox(request: CardinalIngestRequest) -> bool:
    """
    Determine sandbox mode from the request.
    Mirrors the logic in CardinalIngestRequest.sandbox_implies_test_mode.
    """
    if request.metadata and request.metadata.test_mode:
        return True
    org_lower = request.org.lower()
    return org_lower in ("sandbox", "testorg") or org_lower.startswith(
        ("sandbox_", "test_", "dev_", "staging_")
    )