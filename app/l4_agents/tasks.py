"""
app/l4_agents/tasks.py
========================
Celery Periodic & Operational Tasks

These tasks run on schedules or are triggered by external events.
They are separate from the main process_ticket task in worker.py.

worker.py  → process_ticket         — per-ticket LLM pipeline (event-driven)
tasks.py   → everything else        — operational, scheduled, maintenance

Tasks defined here:

    SCHEDULED (Celery Beat):
        beat_poll_streams            — Trigger stream poll every 5 seconds
                                       when running in Beat mode instead of
                                       the direct poll loop in worker.py
        beat_reclaim_idle_messages   — Reclaim stuck messages every 60s
        beat_refresh_risk_profiles   — Recompute stale customer_risk_profile rows
        beat_purge_stale_dedup_keys  — Clean expired dedup log entries from DB
        beat_execution_plan_timeout  — Mark execution plans stuck in 'processing'
                                       as 'failed' after timeout threshold

    ON-DEMAND (called programmatically or via CLI):
        reprocess_ticket             — Manually requeue a specific ticket
        drain_failed_executions      — Inspect and optionally requeue failed plans
        health_check                 — Verify DB + Redis connectivity, return status

Celery Beat schedule is defined at the bottom of this file.
Register with:
    celery -A app.l4_agents.tasks beat --loglevel=info
Or combined worker+beat (single process, dev only):
    celery -A app.l4_agents.tasks worker --beat --loglevel=info
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

from app.admin.redis_client import get_redis
from app.l4_agents.worker import (
    celery_app,
    ensure_consumer_groups,
    poll_streams_once,
    reclaim_idle_messages,
    SCHEMA,
    CONSUMER_NAME,
)

# ============================================================
# ENVIRONMENT
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = os.getenv("DB_PORT", "5432")
DB_NAME     = os.getenv("DB_NAME", "orgintelligence")
DB_USER     = os.getenv("DB_USER", "orguser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Execution plan stuck timeout — plans in 'processing' longer than this are failed
EXECUTION_TIMEOUT_MINUTES = int(os.getenv("EXECUTION_TIMEOUT_MINUTES", "30"))

# Risk profile staleness threshold — recompute if last_computed_at older than this
RISK_PROFILE_STALE_HOURS = int(os.getenv("RISK_PROFILE_STALE_HOURS", "2"))

logger = logging.getLogger("cardinal.tasks")


# ============================================================
# DB CONNECTION
# ============================================================

def _get_connection() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
    )


# ============================================================
# SCHEDULED TASKS
# ============================================================

@celery_app.task(name="app.l4_agents.tasks.beat_poll_streams")
def beat_poll_streams() -> dict[str, int]:
    """
    Trigger a single stream poll cycle.
    Used when running in Celery Beat mode instead of the
    direct __main__ poll loop in worker.py.

    Beat schedules this every 5 seconds.
    Each invocation reads up to STREAM_BATCH_SIZE messages
    per stream and dispatches them as process_ticket tasks.
    """
    ensure_consumer_groups()
    dispatched = poll_streams_once()
    if dispatched:
        logger.info("beat_poll_streams: dispatched %d task(s)", dispatched)
    return {"dispatched": dispatched}


@celery_app.task(name="app.l4_agents.tasks.beat_reclaim_idle_messages")
def beat_reclaim_idle_messages() -> dict[str, int]:
    """
    Reclaim stream messages that were delivered but never acknowledged.
    Handles crashed workers — their pending messages are re-dispatched.

    Beat schedules this every 60 seconds.
    """
    reclaimed = reclaim_idle_messages()
    if reclaimed:
        logger.warning("beat_reclaim_idle_messages: reclaimed %d message(s)", reclaimed)
    return {"reclaimed": reclaimed}


@celery_app.task(name="app.l4_agents.tasks.beat_refresh_risk_profiles")
def beat_refresh_risk_profiles() -> dict[str, int]:
    """
    Recompute customer_risk_profile rows that are stale.

    Staleness = last_computed_at older than RISK_PROFILE_STALE_HOURS.

    Process:
        1. Read unprocessed rows from risk_profile_change_log
        2. For each affected customer_id, recompute aggregates
           from orders, refunds, complaints tables
        3. Upsert into customer_risk_profile
        4. Mark change_log rows as processed

    Beat schedules this every hour.

    Note: This is the computation job referenced in migration 004.
    The triggers log changes; this job processes them.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RISK_PROFILE_STALE_HOURS)
    conn   = _get_connection()
    updated = 0

    try:
        # Step 1: Get customer_ids with unprocessed change log entries
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT customer_id
                FROM {SCHEMA}.risk_profile_change_log
                WHERE processed = false
                LIMIT 500
                """
            )
            rows = cur.fetchall()

        customer_ids = [r[0] for r in rows]
        if not customer_ids:
            return {"updated": 0}

        # Step 2: Recompute each customer's risk profile
        for customer_id in customer_ids:
            try:
                _recompute_risk_profile(conn, customer_id)
                updated += 1
            except Exception as exc:
                logger.error(
                    "Risk profile recompute failed for customer_id=%s: %s",
                    customer_id, exc,
                )

        # Step 3: Mark processed
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.risk_profile_change_log
                    SET processed    = true,
                        processed_at = %s
                    WHERE customer_id = ANY(%s)
                    AND   processed   = false
                    """,
                    (datetime.now(timezone.utc), customer_ids),
                )

        logger.info("beat_refresh_risk_profiles: updated %d profile(s)", updated)
        return {"updated": updated}

    except psycopg2.Error as exc:
        logger.error("beat_refresh_risk_profiles failed: %s", exc)
        return {"updated": updated, "error": str(exc)}
    finally:
        conn.close()


@celery_app.task(name="app.l4_agents.tasks.beat_purge_stale_dedup_keys")
def beat_purge_stale_dedup_keys() -> dict[str, int]:
    """
    Clean up deduplication_log rows older than 30 days.

    Redis keys expire automatically after 24h via TTL.
    The Postgres deduplication_log is permanent by design — but
    rows older than 30 days have no operational value and can
    be archived or deleted to keep the table lean.

    Beat schedules this daily at 02:00 UTC.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    conn   = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    DELETE FROM {SCHEMA}.deduplication_log
                    WHERE created_at < %s
                    """,
                    (cutoff,),
                )
                deleted = cur.rowcount
        logger.info("beat_purge_stale_dedup_keys: deleted %d row(s)", deleted)
        return {"deleted": deleted}
    except psycopg2.Error as exc:
        logger.error("beat_purge_stale_dedup_keys failed: %s", exc)
        return {"deleted": 0, "error": str(exc)}
    finally:
        conn.close()


@celery_app.task(name="app.l4_agents.tasks.beat_execution_plan_timeout")
def beat_execution_plan_timeout() -> dict[str, int]:
    """
    Mark execution plans that have been stuck in 'processing'
    longer than EXECUTION_TIMEOUT_MINUTES as 'failed'.

    This catches:
        - Workers that claimed a ticket but died without updating state
        - Tasks that hit an unhandled exception that bypassed _handle_failure
        - DB deadlocks that caused a worker to hang indefinitely

    Beat schedules this every 10 minutes.
    """
    timeout_cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=EXECUTION_TIMEOUT_MINUTES
    )
    conn  = _get_connection()
    timed_out = 0

    try:
        with conn:
            with conn.cursor() as cur:
                # Find stuck execution plans
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.cardinal_execution_plans
                    SET    status       = 'failed',
                           completed_at = %s,
                           metadata     = COALESCE(metadata, '{{}}'::jsonb)
                                          || '{{"timeout": true}}'::jsonb
                    WHERE  status       = 'processing'
                    AND    started_at   < %s
                    RETURNING execution_id
                    """,
                    (datetime.now(timezone.utc), timeout_cutoff),
                )
                timed_out_ids = [r[0] for r in cur.fetchall()]
                timed_out = len(timed_out_ids)

                # Update corresponding ticket_processing_state rows
                if timed_out_ids:
                    cur.execute(
                        f"""
                        UPDATE {SCHEMA}.ticket_processing_state
                        SET    error_message           = 'Execution timed out',
                               processing_completed_at = %s
                        WHERE  execution_id = ANY(%s)
                        AND    processing_completed_at IS NULL
                        """,
                        (datetime.now(timezone.utc), timed_out_ids),
                    )

        if timed_out:
            logger.warning(
                "beat_execution_plan_timeout: timed out %d execution(s): %s",
                timed_out, timed_out_ids,
            )
        return {"timed_out": timed_out}

    except psycopg2.Error as exc:
        logger.error("beat_execution_plan_timeout failed: %s", exc)
        return {"timed_out": 0, "error": str(exc)}
    finally:
        conn.close()


# ============================================================
# ON-DEMAND TASKS
# ============================================================

@celery_app.task(name="app.l4_agents.tasks.reprocess_ticket")
def reprocess_ticket(
    ticket_id:    int,
    reason:       str = "manual_reprocess",
    requested_by: str = "ops",
) -> dict[str, Any]:
    """
    Manually requeue a specific ticket for reprocessing.

    Use cases:
        - A ticket failed due to a transient error and max retries were hit
        - A policy version was updated and the ticket needs re-evaluation
        - QA found an incorrect resolution and wants it reconsidered

    Fetches the existing execution_id from fdraw, generates a new
    execution_id for the reprocess run, and pushes to P3_STANDARD stream.

    Args:
        ticket_id:    fdraw ticket_id to reprocess
        reason:       Why it's being reprocessed (for audit trail)
        requested_by: Who triggered it (agent ID, ops username, etc.)

    Returns:
        dict with new execution_id and stream message_id, or error info
    """
    from app.l2_cardinal.phase5_dispatcher import _generate_execution_id
    from app.l1_ingestion.schemas import CanonicalPayload

    logger.info(
        "reprocess_ticket | ticket_id=%s | reason=%s | by=%s",
        ticket_id, reason, requested_by,
    )

    conn = _get_connection()
    try:
        # Fetch existing ticket context from fdraw
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT ticket_id, module, canonical_payload, pipeline_stage
                FROM   {SCHEMA}.fdraw
                WHERE  ticket_id = %s
                LIMIT  1
                """,
                (ticket_id,),
            )
            row = cur.fetchone()

        if not row:
            return {"status": "error", "message": f"ticket_id={ticket_id} not found"}

        canonical_payload = row["canonical_payload"] or {}
        execution_id_old  = canonical_payload.get("execution_id", "unknown")

        # Build a minimal CanonicalPayload to generate a new execution_id
        # We only need org and is_sandbox for the ID format
        org        = canonical_payload.get("org", "unknown")
        is_sandbox = canonical_payload.get("is_sandbox", False)

        # Generate new execution_id for this reprocess run
        import time, uuid
        new_execution_id = (
            f"single_{org[:20]}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        )

        # Push to P3_STANDARD stream
        r = get_redis()
        stream_fields = {
            "execution_id":              new_execution_id,
            "ticket_id":                 str(ticket_id),
            "org":                       org,
            "module":                    canonical_payload.get("module", ""),
            "business_line":             canonical_payload.get("business_line", ""),
            "active_policy":             canonical_payload.get("active_policy", ""),
            "customer_id":               canonical_payload.get("customer_id", ""),
            "priority":                  "P3_STANDARD",
            "escalation_group":          "STANDARD",
            "is_sandbox":                str(is_sandbox).lower(),
            "reprocess":                 "true",
            "prior_complaints_30d":      "0",
            "fraud_risk_classification": "NORMAL",
            "auto_approval_limit":       "500.0",
            "recommended_queue":         "STANDARD_REVIEW",
            "enriched_at":               datetime.now(timezone.utc).isoformat(),
            "reprocess_reason":          reason,
            "reprocess_requested_by":    requested_by,
            "original_execution_id":     execution_id_old,
        }

        msg_id = r.xadd(
            name="cardinal:dispatch:P3_STANDARD",
            fields=stream_fields,
        )

        # Write new execution plan row
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.cardinal_execution_plans (
                        execution_id, execution_mode, org,
                        module, total_tickets, status,
                        metadata, created_at
                    ) VALUES (%s, 'single', %s, %s, 1, 'queued', %s, %s)
                    """,
                    (
                        new_execution_id,
                        org,
                        canonical_payload.get("module", ""),
                        psycopg2.extras.Json({
                            "reprocess":              True,
                            "reason":                 reason,
                            "requested_by":           requested_by,
                            "original_execution_id":  execution_id_old,
                        }),
                        datetime.now(timezone.utc),
                    ),
                )
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.ticket_processing_state (
                        ticket_id, execution_id, current_stage,
                        stage_0_status, stage_1_status,
                        stage_2_status, stage_3_status,
                        module, created_at
                    ) VALUES (%s, %s, 0,
                        'pending', 'pending', 'pending', 'pending',
                        %s, %s)
                    """,
                    (
                        ticket_id,
                        new_execution_id,
                        canonical_payload.get("module", ""),
                        datetime.now(timezone.utc),
                    ),
                )

        logger.info(
            "reprocess_ticket queued | ticket_id=%s | new_execution_id=%s | msg_id=%s",
            ticket_id, new_execution_id, msg_id,
        )
        return {
            "status":            "queued",
            "ticket_id":         ticket_id,
            "new_execution_id":  new_execution_id,
            "stream_message_id": msg_id,
        }

    except Exception as exc:
        logger.error("reprocess_ticket failed for ticket_id=%s: %s", ticket_id, exc)
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


@celery_app.task(name="app.l4_agents.tasks.drain_failed_executions")
def drain_failed_executions(
    requeue:       bool = False,
    limit:         int  = 50,
) -> dict[str, Any]:
    """
    Inspect failed execution plans and optionally requeue them.

    Args:
        requeue:  If True, automatically requeue all failed executions
                  via reprocess_ticket. If False, just return the list.
        limit:    Max number of failed executions to inspect/requeue.

    Returns:
        dict with list of failed execution_ids and requeue results.

    Usage:
        # Inspect only
        drain_failed_executions.delay(requeue=False)

        # Inspect and requeue
        drain_failed_executions.delay(requeue=True, limit=10)
    """
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    cep.execution_id,
                    cep.org,
                    cep.module,
                    cep.created_at,
                    cep.completed_at,
                    tps.ticket_id,
                    tps.error_message,
                    tps.retry_count
                FROM {SCHEMA}.cardinal_execution_plans cep
                LEFT JOIN {SCHEMA}.ticket_processing_state tps
                    ON cep.execution_id = tps.execution_id
                WHERE cep.status = 'failed'
                ORDER BY cep.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            return {"failed_count": 0, "executions": []}

        requeue_results = []
        if requeue:
            for row in rows:
                if row.get("ticket_id"):
                    result = reprocess_ticket.delay(
                        ticket_id=row["ticket_id"],
                        reason="drain_failed_executions",
                        requested_by="system",
                    )
                    requeue_results.append({
                        "ticket_id":    row["ticket_id"],
                        "execution_id": row["execution_id"],
                        "task_id":      result.id,
                    })

        return {
            "failed_count":   len(rows),
            "executions":     rows,
            "requeued":       len(requeue_results),
            "requeue_results": requeue_results,
        }

    except psycopg2.Error as exc:
        logger.error("drain_failed_executions failed: %s", exc)
        return {"failed_count": 0, "error": str(exc)}
    finally:
        conn.close()


@celery_app.task(name="app.l4_agents.tasks.health_check")
def health_check() -> dict[str, Any]:
    """
    Verify DB + Redis connectivity from within a worker process.
    Useful for confirming workers are alive and connected.

    Returns status dict — safe to call frequently.
    """
    status: dict[str, Any] = {
        "worker":   CONSUMER_NAME,
        "database": "unknown",
        "redis":    "unknown",
        "streams":  {},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    # DB check
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        status["database"] = "connected"
    except Exception as exc:
        status["database"] = f"error: {exc}"

    # Redis check + stream lengths
    try:
        r = get_redis()
        r.ping()
        status["redis"] = "connected"

        for stream in [
            "cardinal:dispatch:P1_CRITICAL",
            "cardinal:dispatch:P2_HIGH",
            "cardinal:dispatch:P3_STANDARD",
            "cardinal:dispatch:P4_LOW",
        ]:
            try:
                length = r.xlen(stream)
                status["streams"][stream] = {"length": length}
            except Exception:
                status["streams"][stream] = {"length": "unknown"}

    except Exception as exc:
        status["redis"] = f"error: {exc}"

    return status


# ============================================================
# RISK PROFILE COMPUTATION HELPER
# ============================================================

def _recompute_risk_profile(
    conn:        psycopg2.extensions.connection,
    customer_id: str,
) -> None:
    """
    Recompute a single customer's risk profile from source tables.
    Called by beat_refresh_risk_profiles for each affected customer.

    Aggregates:
        - orders_last_7/30/90_days   from orders table
        - refunds_last_7/30/90_days  from complaints table (resolved refunds)
        - refund_rate_7/30/90d       computed from above
        - complaints_last_30_days    from complaints table
        - marked_delivered_claims_90d from complaints WHERE issue_type_l2='not_received'

    fraud_score and fraud_risk_classification are computed from
    refund_rate_30d + marked_delivered_claims_90d using simple
    threshold rules. Full ML scoring replaces this in Phase 3 roadmap.
    """
    now = datetime.now(timezone.utc)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Order counts
        cur.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE created_at >= %s - INTERVAL '7 days')  AS orders_7d,
                COUNT(*) FILTER (WHERE created_at >= %s - INTERVAL '30 days') AS orders_30d,
                COUNT(*) FILTER (WHERE created_at >= %s - INTERVAL '90 days') AS orders_90d
            FROM {SCHEMA}.orders
            WHERE customer_id = %s
            """,
            (now, now, now, customer_id),
        )
        order_row = cur.fetchone()

        # Refund counts (complaints with a refund action)
        cur.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE raised_at >= %s - INTERVAL '7 days')  AS refunds_7d,
                COUNT(*) FILTER (WHERE raised_at >= %s - INTERVAL '30 days') AS refunds_30d,
                COUNT(*) FILTER (WHERE raised_at >= %s - INTERVAL '90 days') AS refunds_90d,
                COUNT(*) FILTER (WHERE raised_at >= %s - INTERVAL '30 days') AS complaints_30d,
                COUNT(*) FILTER (
                    WHERE raised_at >= %s - INTERVAL '90 days'
                    AND   issue_type_l2 = 'not_received'
                ) AS marked_delivered_90d
            FROM {SCHEMA}.complaints
            WHERE customer_id = %s
            AND   action_code LIKE 'REFUND%%'
            """,
            (now, now, now, now, now, customer_id),
        )
        refund_row = cur.fetchone()

    orders_7d  = order_row["orders_7d"]  or 0
    orders_30d = order_row["orders_30d"] or 0
    orders_90d = order_row["orders_90d"] or 0
    refunds_7d  = refund_row["refunds_7d"]  or 0
    refunds_30d = refund_row["refunds_30d"] or 0
    refunds_90d = refund_row["refunds_90d"] or 0
    complaints_30d       = refund_row["complaints_30d"]       or 0
    marked_delivered_90d = refund_row["marked_delivered_90d"] or 0

    # Refund rates (avoid divide by zero)
    rr_7d  = round(refunds_7d  / orders_7d,  4) if orders_7d  > 0 else 0.0
    rr_30d = round(refunds_30d / orders_30d, 4) if orders_30d > 0 else 0.0
    rr_90d = round(refunds_90d / orders_90d, 4) if orders_90d > 0 else 0.0

    # Simple fraud score: weighted refund rate + marked_delivered signal
    fraud_score = min(1.0, round(
        (rr_30d * 0.5)
        + (marked_delivered_90d * 0.05)
        + (rr_90d * 0.3),
        3
    ))

    if fraud_score >= 0.7:
        fraud_class = "HIGH_RISK"
        auto_limit  = 0.0
        queue       = "FRAUD_TEAM"
    elif fraud_score >= 0.4:
        fraud_class = "MEDIUM_RISK"
        auto_limit  = 200.0
        queue       = "L2_TEAM_LEAD"
    elif fraud_score >= 0.2:
        fraud_class = "LOW_RISK"
        auto_limit  = 200.0
        queue       = "STANDARD_REVIEW"
    else:
        fraud_class = "NORMAL"
        auto_limit  = 500.0
        queue       = "STANDARD_REVIEW"

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA}.customer_risk_profile (
                    customer_id,
                    fraud_score, fraud_risk_classification,
                    orders_last_7_days, orders_last_30_days, orders_last_90_days,
                    refunds_last_7_days, refunds_last_30_days, refunds_last_90_days,
                    refund_rate_7d, refund_rate_30d, refund_rate_90d,
                    complaints_last_30_days, marked_delivered_claims_90d,
                    auto_approval_eligible, auto_approval_limit,
                    recommended_queue, last_computed_at
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (customer_id) DO UPDATE SET
                    fraud_score                  = EXCLUDED.fraud_score,
                    fraud_risk_classification    = EXCLUDED.fraud_risk_classification,
                    orders_last_7_days           = EXCLUDED.orders_last_7_days,
                    orders_last_30_days          = EXCLUDED.orders_last_30_days,
                    orders_last_90_days          = EXCLUDED.orders_last_90_days,
                    refunds_last_7_days          = EXCLUDED.refunds_last_7_days,
                    refunds_last_30_days         = EXCLUDED.refunds_last_30_days,
                    refunds_last_90_days         = EXCLUDED.refunds_last_90_days,
                    refund_rate_7d               = EXCLUDED.refund_rate_7d,
                    refund_rate_30d              = EXCLUDED.refund_rate_30d,
                    refund_rate_90d              = EXCLUDED.refund_rate_90d,
                    complaints_last_30_days      = EXCLUDED.complaints_last_30_days,
                    marked_delivered_claims_90d  = EXCLUDED.marked_delivered_claims_90d,
                    auto_approval_eligible       = EXCLUDED.auto_approval_eligible,
                    auto_approval_limit          = EXCLUDED.auto_approval_limit,
                    recommended_queue            = EXCLUDED.recommended_queue,
                    last_computed_at             = EXCLUDED.last_computed_at
                """,
                (
                    customer_id,
                    fraud_score, fraud_class,
                    orders_7d, orders_30d, orders_90d,
                    refunds_7d, refunds_30d, refunds_90d,
                    rr_7d, rr_30d, rr_90d,
                    complaints_30d, marked_delivered_90d,
                    fraud_score < 0.7, auto_limit,
                    queue, now,
                ),
            )


# ============================================================
# CELERY BEAT SCHEDULE
# ============================================================

celery_app.conf.beat_schedule = {

    "poll-streams-every-5s": {
        "task":     "app.l4_agents.tasks.beat_poll_streams",
        "schedule": 5.0,   # seconds
    },

    "reclaim-idle-every-60s": {
        "task":     "app.l4_agents.tasks.beat_reclaim_idle_messages",
        "schedule": 60.0,
    },

    "refresh-risk-profiles-hourly": {
        "task":     "app.l4_agents.tasks.beat_refresh_risk_profiles",
        "schedule": crontab(minute=0),   # top of every hour
    },

    "timeout-stuck-executions-every-10m": {
        "task":     "app.l4_agents.tasks.beat_execution_plan_timeout",
        "schedule": 600.0,  # 10 minutes
    },

    "purge-stale-dedup-keys-daily": {
        "task":     "app.l4_agents.tasks.beat_purge_stale_dedup_keys",
        "schedule": crontab(hour=2, minute=0),  # 02:00 UTC daily
    },
}