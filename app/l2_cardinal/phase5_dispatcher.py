"""
app/l2_cardinal/phase5_dispatcher.py
======================================
Cardinal Phase 5 — Dispatcher

Responsibility:
    1. Generate execution_id           — unique identifier for this processing attempt
    2. Determine priority              — based on risk profile and customer tier
    3. Assign escalation_group         — groups complaints that share one LLM execution
    4. Write cardinal_execution_plans  — the execution plan record
    5. Write ticket_processing_state   — per-ticket stage tracker
    6. Dispatch to Redis Streams       — the actual handoff to the Celery worker
    7. Update fdraw.pipeline_stage     → DISPATCHED

This is the final Cardinal phase. After this returns, the Cardinal's
job is done. The Celery worker picks up the stream message and drives
the L4 LLM pipeline.

This module does NOT:
    - Run any LLM
    - Make business rule decisions
    - Block waiting for worker completion (fire-and-forget dispatch)

Position in pipeline:
    Phase 4 (enricher) → Phase 5 (here) → Redis Stream → Celery Worker

Execution ID format (from API_CONTRACT_FINAL.md):
    {mode}_{org}_{timestamp}_{uuid8}
    e.g.  single_AcmeCorp_1771953892_a7b3c9d2
          single_Sandbox_1771954000_b8c4d3e1
          batch_AcmeCorp_1771954100_c9d5e2f3

Priority levels:
    P1_CRITICAL  — Fraud HIGH_RISK or BLOCKED customer (rare — these are
                   usually rejected earlier, but may slip through if risk
                   profile was stale)
    P2_HIGH      — VIP customer, or prior_complaints >= 3 in 30 days
                   (serial issue — needs fast human review)
    P3_STANDARD  — Default path
    P4_LOW       — Sandbox/test executions

Redis Stream:
    Stream name:  cardinal:dispatch:{priority}
    One stream per priority level — Celery workers consume based on
    their configured priority. High-priority workers poll P1+P2 first.

    Message fields written to stream:
        execution_id, ticket_id, org, module, business_line,
        active_policy, customer_id, priority, escalation_group,
        is_sandbox, reprocess, prior_complaints_30d, enriched_at

Escalation group assignment:
    escalation_group determines which complaints get grouped into
    one LLM execution vs separated into independent executions.

    Rules (evaluated in order):
      FRAUD_REVIEW      — fraud_risk_classification in (HIGH_RISK, BLOCKED)
      VIP_CONCIERGE     — vip_flag=True or membership_tier=PREMIUM/GOLD
      REPEAT_ESCALATION — prior_complaints_30d >= 3
      STANDARD          — default
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from app.admin.redis_client import get_redis
from app.l1_ingestion.schemas import CanonicalPayload
from app.l2_cardinal.phase4_enricher import Phase4Result, CustomerContext

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

# Redis stream names — one per priority level
STREAM_P1 = "cardinal:dispatch:P1_CRITICAL"
STREAM_P2 = "cardinal:dispatch:P2_HIGH"
STREAM_P3 = "cardinal:dispatch:P3_STANDARD"
STREAM_P4 = "cardinal:dispatch:P4_LOW"

# Redis stream maxlen — cap stream size to prevent unbounded growth
# Celery workers consume and acknowledge — this is a safety cap only
STREAM_MAXLEN = 10_000

logger = logging.getLogger("cardinal.phase5")


# ============================================================
# CONSTANTS
# ============================================================

class Priority:
    CRITICAL = "P1_CRITICAL"
    HIGH     = "P2_HIGH"
    STANDARD = "P3_STANDARD"
    LOW      = "P4_LOW"


class EscalationGroup:
    FRAUD_REVIEW      = "FRAUD_REVIEW"
    VIP_CONCIERGE     = "VIP_CONCIERGE"
    REPEAT_ESCALATION = "REPEAT_ESCALATION"
    STANDARD          = "STANDARD"


STREAM_MAP = {
    Priority.CRITICAL: STREAM_P1,
    Priority.HIGH:     STREAM_P2,
    Priority.STANDARD: STREAM_P3,
    Priority.LOW:      STREAM_P4,
}


# ============================================================
# RESULT DATACLASS
# ============================================================

@dataclass
class Phase5Result:
    """
    Final output of the Cardinal pipeline.
    Returned by pipeline.py to the router, which returns it to the caller.
    """
    execution_id:      str
    ticket_id:         int
    org:               str
    priority:          str
    escalation_group:  str
    active_policy:     str
    stream_name:       str            # Which Redis stream received the message
    stream_message_id: str            # Redis XADD return value (stream sequence ID)
    is_sandbox:        bool
    warnings:          list[str]      = field(default_factory=list)
    dispatched_at:     str            = ""


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

def run(phase4: Phase4Result) -> Phase5Result:
    """
    Execute Phase 5: Dispatcher.

    Steps:
      1. Generate execution_id.
      2. Determine priority from CustomerContext.
      3. Assign escalation_group.
      4. Write cardinal_execution_plans row.
      5. Write ticket_processing_state row.
      6. Dispatch to Redis Stream.
      7. Update fdraw.pipeline_stage → DISPATCHED + execution_id.

    Args:
        phase4:  Phase4Result from Phase 4.

    Returns:
        Phase5Result — the final Cardinal output. pipeline.py returns
        this (serialised) to the caller as the 202 Accepted response.

    Raises:
        DispatchError: If Redis stream push fails. DB write failures
                       are logged but do not raise — Redis dispatch
                       is the only hard dependency here. A ticket
                       without a DB execution plan record but with
                       a Redis message is recoverable. A ticket
                       without a Redis message is lost.
    """
    canonical = phase4.canonical
    context   = phase4.context
    warnings  = list(phase4.warnings)  # carry forward all prior phase warnings

    # ----------------------------------------------------------
    # Step 1: Generate execution_id
    # ----------------------------------------------------------
    execution_id = _generate_execution_id(canonical)

    # ----------------------------------------------------------
    # Step 2: Priority
    # ----------------------------------------------------------
    priority = _determine_priority(context, canonical.is_sandbox)

    # ----------------------------------------------------------
    # Step 3: Escalation group
    # ----------------------------------------------------------
    escalation_group = _assign_escalation_group(context)

    # ----------------------------------------------------------
    # Step 4: Write cardinal_execution_plans
    # ----------------------------------------------------------
    _write_execution_plan(
        execution_id=execution_id,
        canonical=canonical,
        priority=priority,
        active_policy=phase4.active_policy,
    )

    # ----------------------------------------------------------
    # Step 5: Write ticket_processing_state
    # ----------------------------------------------------------
    _write_ticket_processing_state(
        execution_id=execution_id,
        ticket_id=canonical.ticket_id,
        module=canonical.module,
    )

    # ----------------------------------------------------------
    # Step 6: Dispatch to Redis Stream (hard dependency)
    # ----------------------------------------------------------
    stream_name = STREAM_MAP[priority]
    stream_message_id = _dispatch_to_stream(
        stream_name=stream_name,
        execution_id=execution_id,
        canonical=canonical,
        context=context,
        priority=priority,
        escalation_group=escalation_group,
        active_policy=phase4.active_policy,
    )

    # ----------------------------------------------------------
    # Step 7: Update fdraw
    # ----------------------------------------------------------
    dispatched_at = datetime.now(timezone.utc).isoformat()
    _update_fdraw(canonical.ticket_id, execution_id, dispatched_at)

    logger.info(
        "Phase 5 complete | execution_id=%s | ticket_id=%s | "
        "priority=%s | escalation_group=%s | stream=%s | msg_id=%s",
        execution_id,
        canonical.ticket_id,
        priority,
        escalation_group,
        stream_name,
        stream_message_id,
    )

    return Phase5Result(
        execution_id=execution_id,
        ticket_id=canonical.ticket_id,
        org=canonical.org,
        priority=priority,
        escalation_group=escalation_group,
        active_policy=phase4.active_policy,
        stream_name=stream_name,
        stream_message_id=stream_message_id,
        is_sandbox=canonical.is_sandbox,
        warnings=warnings,
        dispatched_at=dispatched_at,
    )


# ============================================================
# EXECUTION ID GENERATION
# ============================================================

def _generate_execution_id(canonical: CanonicalPayload) -> str:
    """
    Generate execution_id per API_CONTRACT_FINAL.md format:
        {mode}_{org}_{timestamp}_{uuid8}

    mode:      'single' for all ingest-path tickets.
               'batch' reserved for future bulk import endpoint.
    org:       canonical.org — org name as provided by caller.
               Spaces replaced with underscores, length capped at 20.
    timestamp: Unix timestamp (integer seconds).
    uuid8:     First 8 chars of a UUID4 hex digest.
    """
    mode      = "single"
    org_slug  = canonical.org.replace(" ", "_")[:20]
    timestamp = int(time.time())
    uid       = uuid.uuid4().hex[:8]

    return f"{mode}_{org_slug}_{timestamp}_{uid}"


# ============================================================
# PRIORITY DETERMINATION
# ============================================================

def _determine_priority(context: CustomerContext, is_sandbox: bool) -> str:
    """
    Map CustomerContext signals to a priority level.

    Evaluated in order — first match wins.

    P4_LOW (sandbox):
      - is_sandbox=True
      No real customer, no real order. Test traffic goes to
      the lowest priority stream so it doesn't consume worker
      capacity ahead of real tickets.

    P1_CRITICAL:
      - fraud_risk_classification in (HIGH_RISK, BLOCKED)
      These tickets need immediate human review — automated
      resolution is blocked. Routing to FRAUD_TEAM queue.

    P2_HIGH:
      - vip_flag=True
      - membership_tier in (PREMIUM, GOLD)
      - prior_complaints_30d >= 3 (serial issue — customer already
        frustrated, fast resolution reduces churn risk)
      - churn_probability >= 0.7 (high churn risk customer)

    P3_STANDARD:
      - Everything else
    """
    if is_sandbox:
        return Priority.LOW

    risk     = context.risk
    customer = context.customer

    # P1: High-risk fraud
    if risk.fraud_risk_classification in ("HIGH_RISK", "BLOCKED"):
        return Priority.CRITICAL

    # P2: VIP / tier / repeat / churn
    if (
        customer.vip_flag
        or customer.membership_tier in ("PREMIUM", "GOLD")
        or context.prior_complaints_30d >= 3
        or (customer.churn_probability is not None and customer.churn_probability >= 0.7)
    ):
        return Priority.HIGH

    return Priority.STANDARD


# ============================================================
# ESCALATION GROUP ASSIGNMENT
# ============================================================

def _assign_escalation_group(context: CustomerContext) -> str:
    """
    Assign escalation_group — determines routing queue and
    how complaints from this ticket are grouped for LLM execution.

    FRAUD_REVIEW:      fraud_risk_classification HIGH_RISK or BLOCKED
    VIP_CONCIERGE:     vip_flag=True or membership_tier PREMIUM/GOLD
    REPEAT_ESCALATION: prior_complaints_30d >= 3
    STANDARD:          everything else

    Note: escalation_group is written to cardinal_execution_plans.metadata
    and to the Redis stream message. The Celery worker reads it to
    determine which complaints table queue column to populate.
    """
    risk     = context.risk
    customer = context.customer

    if risk.fraud_risk_classification in ("HIGH_RISK", "BLOCKED"):
        return EscalationGroup.FRAUD_REVIEW

    if customer.vip_flag or customer.membership_tier in ("PREMIUM", "GOLD"):
        return EscalationGroup.VIP_CONCIERGE

    if context.prior_complaints_30d >= 3:
        return EscalationGroup.REPEAT_ESCALATION

    return EscalationGroup.STANDARD


# ============================================================
# DB WRITES
# ============================================================

def _write_execution_plan(
    execution_id: str,
    canonical:    CanonicalPayload,
    priority:     str,
    active_policy: str,
) -> None:
    """
    Insert a row into cardinal_execution_plans.

    status starts as 'queued' — the Celery worker updates it to
    'processing' when it claims the message, and 'completed' or
    'failed' when done.

    Failure here is logged but does not raise — the execution plan
    record is useful for auditing but the Redis message is the
    authoritative dispatch signal. A missing execution plan row
    is recoverable from the stream history.
    """
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.cardinal_execution_plans (
                        execution_id,
                        execution_mode,
                        org,
                        business_line,
                        module,
                        total_tickets,
                        worker_count,
                        current_stage,
                        status,
                        metadata,
                        created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        1, 1, 0, 'queued',
                        %s, %s
                    )
                    """,
                    (
                        execution_id,
                        "single",
                        canonical.org,
                        canonical.business_line,
                        canonical.module,
                        psycopg2.extras.Json({
                            "priority":       priority,
                            "active_policy":  active_policy,
                            "is_sandbox":     canonical.is_sandbox,
                            "ticket_id":      canonical.ticket_id,
                            "thread_id":      canonical.thread_id,
                            "reprocess":      canonical.reprocess,
                        }),
                        datetime.now(timezone.utc),
                    ),
                )
    except psycopg2.Error as exc:
        logger.error(
            "Failed to write cardinal_execution_plans for execution_id=%s: %s",
            execution_id, exc,
        )
        # Do not raise — Redis dispatch is the hard dependency


def _write_ticket_processing_state(
    execution_id: str,
    ticket_id:    int,
    module:       str,
) -> None:
    """
    Insert a row into ticket_processing_state.

    This is the per-ticket stage tracker that the Celery worker
    updates as it moves through Stage 0 → Stage 1 → Stage 2 → Stage 3.

    All stage statuses start as 'pending'. The worker updates them
    atomically as each stage completes.

    Failure here is logged but does not raise.
    """
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.ticket_processing_state (
                        ticket_id,
                        execution_id,
                        current_stage,
                        stage_0_status,
                        stage_1_status,
                        stage_2_status,
                        stage_3_status,
                        module,
                        created_at
                    ) VALUES (
                        %s, %s, 0,
                        'pending', 'pending', 'pending', 'pending',
                        %s, %s
                    )
                    """,
                    (
                        ticket_id,
                        execution_id,
                        module,
                        datetime.now(timezone.utc),
                    ),
                )
    except psycopg2.Error as exc:
        logger.error(
            "Failed to write ticket_processing_state for ticket_id=%s "
            "execution_id=%s: %s",
            ticket_id, execution_id, exc,
        )
        # Do not raise


# ============================================================
# REDIS STREAM DISPATCH
# ============================================================

def _dispatch_to_stream(
    stream_name:     str,
    execution_id:    str,
    canonical:       CanonicalPayload,
    context:         CustomerContext,
    priority:        str,
    escalation_group: str,
    active_policy:   str,
) -> str:
    """
    Push a message onto the appropriate Redis Stream.

    Redis Streams use XADD — returns the stream entry ID
    (e.g. '1771953892000-0'). The Celery worker consumes
    via XREADGROUP with consumer group 'cardinal_workers'.

    The message contains everything the Celery worker needs
    to begin processing without querying the DB again:
        - execution_id: the unique run identifier
        - ticket_id: fdraw primary key
        - org, module, business_line: routing context
        - active_policy: which rule set to apply
        - customer_id: resolved identifier
        - priority, escalation_group: worker routing
        - is_sandbox, reprocess: processing mode flags
        - fraud_risk_classification: fast fraud routing
        - auto_approval_limit: cap for Stage 1 (evaluation)
        - recommended_queue: where to route HITL if needed
        - prior_complaints_30d: for escalation decisions

    Redis Streams values must be strings — all values are
    explicitly converted to str before XADD.

    Raises DispatchError on Redis failure — this is the only
    hard dependency in Phase 5.
    """
    message = {
        "execution_id":            execution_id,
        "ticket_id":               str(canonical.ticket_id),
        "org":                     canonical.org,
        "module":                  canonical.module,
        "business_line":           canonical.business_line,
        "active_policy":           active_policy,
        "customer_id":             str(canonical.customer_id or ""),
        "priority":                priority,
        "escalation_group":        escalation_group,
        "is_sandbox":              str(canonical.is_sandbox).lower(),
        "reprocess":               str(canonical.reprocess).lower(),
        "prior_complaints_30d":    str(context.prior_complaints_30d),
        "fraud_risk_classification": context.risk.fraud_risk_classification,
        "auto_approval_limit":     str(context.risk.auto_approval_limit),
        "recommended_queue":       context.risk.recommended_queue,
        "enriched_at":             context.enriched_at,
    }

    try:
        r = get_redis()
        msg_id = r.xadd(
            name=stream_name,
            fields=message,
            maxlen=STREAM_MAXLEN,
            approximate=True,   # ~maxlen — allows Redis to batch trim for performance
        )
        logger.debug(
            "Dispatched to stream=%s | execution_id=%s | msg_id=%s",
            stream_name, execution_id, msg_id,
        )
        return msg_id

    except Exception as exc:
        raise DispatchError(
            f"Redis stream dispatch failed for execution_id={execution_id} "
            f"stream={stream_name}: {exc}"
        ) from exc


# ============================================================
# FDRAW UPDATE
# ============================================================

def _update_fdraw(ticket_id: int, execution_id: str, dispatched_at: str) -> None:
    """
    Write execution_id and DISPATCHED stage back to fdraw.
    Also stamps dispatched_at into canonical_payload for audit trail.

    Failure is logged but does not raise — the Redis dispatch
    has already succeeded at this point.
    """
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.fdraw
                    SET
                        pipeline_stage    = 'DISPATCHED',
                        canonical_payload = canonical_payload || %s::jsonb
                    WHERE ticket_id = %s
                    """,
                    (
                        psycopg2.extras.Json({
                            "execution_id":   execution_id,
                            "dispatched_at":  dispatched_at,
                        }),
                        ticket_id,
                    ),
                )
    except psycopg2.Error as exc:
        logger.warning(
            "fdraw dispatch update failed for ticket_id=%s execution_id=%s: %s",
            ticket_id, execution_id, exc,
        )


# ============================================================
# EXCEPTIONS
# ============================================================

class DispatchError(Exception):
    """
    Raised when Redis stream push fails.
    pipeline.py catches this and returns 503.
    A DispatchError means the ticket was ingested and enriched
    but NOT queued for processing — the caller must retry.
    """
    pass