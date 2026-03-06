"""
app/l4_agents/worker.py
=========================
Celery Worker — Cardinal Stream Consumer

This worker:
    1. Polls four prioritised Redis Streams for dispatch messages
    2. Claims each message atomically (prevents double-processing)
    3. Fetches full ticket context from fdraw
    4. Fetches applicable rules from rule_registry
    5. Runs the 4-stage LLM pipeline:
           Stage 0 — Classification   (MODEL1: gpt-4o-mini)
           Stage 1 — Evaluation       (MODEL2: gpt-4.1)
           Stage 2 — Validation       (MODEL3: o3-mini)
           Stage 3 — Response Gen     (MODEL4: gpt-4o)
    6. Writes results to llm_output_1/2/3 and complaints
    7. Updates ticket_processing_state and cardinal_execution_plans
    8. Acknowledges the Redis Stream message on success

Stream consumption model:
    Consumer group:  cardinal_workers
    Consumer name:   worker_{hostname}_{pid}
    Streams polled (in priority order, single XREADGROUP call):
        cardinal:dispatch:P1_CRITICAL
        cardinal:dispatch:P2_HIGH
        cardinal:dispatch:P3_STANDARD
        cardinal:dispatch:P4_LOW

    Workers configured with WORKER_PRIORITY env var poll only their
    tier and below. Default: all four streams.

Celery is used for:
    - Distributed worker pool (scale horizontally)
    - Retry logic with exponential backoff
    - Task routing by priority queue
    - Dead-letter handling on max retries exceeded

Celery broker: Redis (same instance, different DB index)
    Broker URL:  redis://localhost:6379/1  (DB 1, streams on DB 0)

The Celery task wraps the stream consumer loop so that:
    - Each message becomes one Celery task execution
    - Retries are handled by Celery (not manual loop logic)
    - Failures are visible in Flower dashboard

Stage model assignments (from .env):
    Stage 0 — MODEL1 (gpt-4o-mini)   Fast classification, low cost
    Stage 1 — MODEL2 (gpt-4.1)       Evaluation with full context
    Stage 2 — MODEL3 (o3-mini)       Validation + logic checking
    Stage 3 — MODEL4 (gpt-4o)        Response generation (HITL cases)
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from celery import Celery
from dotenv import load_dotenv

from app.admin.redis_client import get_redis

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
SCHEMA      = "kirana_kart"

# Model assignments per stage
MODEL_STAGE_0 = os.getenv("MODEL1", "gpt-4o-mini")   # Classification
MODEL_STAGE_1 = os.getenv("MODEL2", "gpt-4.1")        # Evaluation
MODEL_STAGE_2 = os.getenv("MODEL3", "o3-mini")         # Validation
MODEL_STAGE_3 = os.getenv("MODEL4", "gpt-4o")          # Response generation

REDIS_URL        = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER    = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_BACKEND   = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# Stream names — must match phase5_dispatcher.py exactly
STREAMS = [
    "cardinal:dispatch:P1_CRITICAL",
    "cardinal:dispatch:P2_HIGH",
    "cardinal:dispatch:P3_STANDARD",
    "cardinal:dispatch:P4_LOW",
]

CONSUMER_GROUP = "cardinal_workers"
CONSUMER_NAME  = f"worker_{socket.gethostname()}_{os.getpid()}"

# How many stream messages to fetch per poll cycle
STREAM_BATCH_SIZE = 1   # One message at a time — each becomes one Celery task

# Claim idle messages older than this (ms) — reclaim from crashed workers
CLAIM_IDLE_MS = 60_000  # 60 seconds

MAX_RETRIES = 3

logger = logging.getLogger("cardinal.worker")


# ============================================================
# CELERY APP
# ============================================================

celery_app = Celery(
    "cardinal_worker",
    broker=CELERY_BROKER,
    backend=CELERY_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,          # Ack only after task completes — prevents loss on crash
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, # One task at a time per worker thread — prevents starvation
    task_routes={
        "app.l4_agents.worker.process_ticket": {"queue": "cardinal"},
    },
)


# ============================================================
# DB CONNECTION
# ============================================================

def _get_connection() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
    )


# ============================================================
# STREAM CONSUMER SETUP
# ============================================================

def ensure_consumer_groups() -> None:
    """
    Create consumer groups on all four streams if they don't exist.
    Safe to call on every worker startup — XGROUP CREATE MKSTREAM
    creates the stream if it doesn't exist yet.
    Called once at worker startup before the poll loop begins.
    """
    r = get_redis()
    for stream in STREAMS:
        try:
            r.xgroup_create(
                name=stream,
                groupname=CONSUMER_GROUP,
                id="0",          # Start from beginning of stream
                mkstream=True,   # Create stream if it doesn't exist
            )
            logger.info("Consumer group created: stream=%s group=%s", stream, CONSUMER_GROUP)
        except Exception as exc:
            if "BUSYGROUP" in str(exc):
                pass  # Group already exists — normal on restart
            else:
                logger.warning("xgroup_create error for %s: %s", stream, exc)


# ============================================================
# STREAM POLL LOOP
# ============================================================

def poll_streams_once() -> int:
    """
    Read one batch of messages from all streams (priority order preserved
    because STREAMS list is ordered P1→P4).

    For each message received:
        - Dispatch as a Celery task (process_ticket.delay)
        - The Celery task handles ACK after successful processing

    Returns number of messages dispatched.
    """
    r = get_redis()
    dispatched = 0

    # Build stream dict for XREADGROUP — ">" means only undelivered messages
    stream_ids = {stream: ">" for stream in STREAMS}

    try:
        results = r.xreadgroup(
            groupname=CONSUMER_GROUP,
            consumername=CONSUMER_NAME,
            streams=stream_ids,
            count=STREAM_BATCH_SIZE,
            block=2000,   # Block up to 2 seconds if no messages — prevents tight loop
        )
    except Exception as exc:
        logger.error("xreadgroup failed: %s", exc)
        return 0

    if not results:
        return 0

    for stream_name, messages in results:
        for msg_id, fields in messages:
            # Decode bytes if needed (redis-py may return bytes)
            if isinstance(stream_name, bytes):
                stream_name = stream_name.decode()
            if isinstance(msg_id, bytes):
                msg_id = msg_id.decode()
            fields = {
                (k.decode() if isinstance(k, bytes) else k):
                (v.decode() if isinstance(v, bytes) else v)
                for k, v in fields.items()
            }

            logger.info(
                "Stream message received | stream=%s | msg_id=%s | "
                "execution_id=%s | ticket_id=%s",
                stream_name,
                msg_id,
                fields.get("execution_id", "?"),
                fields.get("ticket_id", "?"),
            )

            # Dispatch to Celery — non-blocking
            process_ticket.delay(
                stream_name=stream_name,
                msg_id=msg_id,
                fields=fields,
            )
            dispatched += 1

    return dispatched


def reclaim_idle_messages() -> int:
    """
    Reclaim messages that were delivered to a worker but never acknowledged.
    This handles crashed workers — their pending messages are re-queued.

    Called periodically (every ~60 seconds) by the poll loop.
    Returns number of messages reclaimed.
    """
    r = get_redis()
    reclaimed = 0

    for stream in STREAMS:
        try:
            # XAUTOCLAIM: reclaim messages idle > CLAIM_IDLE_MS
            # Returns (next_cursor, messages, deleted_ids)
            result = r.xautoclaim(
                name=stream,
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                min_idle_time=CLAIM_IDLE_MS,
                start_id="0-0",
                count=10,
            )
            messages = result[1] if result else []
            for msg_id, fields in messages:
                if isinstance(msg_id, bytes):
                    msg_id = msg_id.decode()
                fields = {
                    (k.decode() if isinstance(k, bytes) else k):
                    (v.decode() if isinstance(v, bytes) else v)
                    for k, v in fields.items()
                }
                logger.warning(
                    "Reclaiming idle message | stream=%s | msg_id=%s | "
                    "execution_id=%s",
                    stream, msg_id, fields.get("execution_id", "?"),
                )
                process_ticket.delay(
                    stream_name=stream,
                    msg_id=msg_id,
                    fields=fields,
                )
                reclaimed += 1
        except Exception as exc:
            logger.warning("xautoclaim failed for %s: %s", stream, exc)

    return reclaimed


# ============================================================
# CELERY TASK — MAIN ENTRY POINT
# ============================================================

@celery_app.task(
    name="app.l4_agents.worker.process_ticket",
    bind=True,
    max_retries=MAX_RETRIES,
    default_retry_delay=30,   # 30s base, Celery applies exponential backoff
    acks_late=True,
)
def process_ticket(
    self,
    stream_name: str,
    msg_id:      str,
    fields:      dict[str, str],
) -> dict[str, Any]:
    """
    Main Celery task. Processes one ticket through all four LLM stages.

    Args:
        stream_name: Which stream this message came from
        msg_id:      Redis Stream message ID (for ACK on completion)
        fields:      Stream message fields from Phase 5 dispatcher

    Returns:
        dict with execution_id, ticket_id, final_action_code, status

    On failure: retries up to MAX_RETRIES with exponential backoff.
    On max retries exceeded: writes to dead-letter store, ACKs message
    (to prevent infinite redelivery), marks ticket as FAILED.
    """
    execution_id = fields.get("execution_id", "unknown")
    ticket_id    = int(fields.get("ticket_id", 0))

    logger.info(
        "Task start | execution_id=%s | ticket_id=%s | stream=%s",
        execution_id, ticket_id, stream_name,
    )

    try:
        # ----------------------------------------------------------
        # Claim ticket in processing state
        # ----------------------------------------------------------
        _claim_ticket(execution_id, ticket_id)

        # ----------------------------------------------------------
        # Fetch full context from fdraw
        # ----------------------------------------------------------
        ticket_context = _fetch_ticket_context(ticket_id)
        if not ticket_context:
            raise WorkerError(f"ticket_id={ticket_id} not found in fdraw")

        # ----------------------------------------------------------
        # Fetch rules from rule_registry
        # ----------------------------------------------------------
        rules = _fetch_rules(
            policy_version=fields.get("active_policy", ""),
            module=fields.get("module", ""),
            business_line=fields.get("business_line", ""),
            fraud_segment=fields.get("fraud_risk_classification", "NORMAL"),
        )

        # ----------------------------------------------------------
        # Stage 0: Classification
        # ----------------------------------------------------------
        _update_stage_status(execution_id, ticket_id, stage=0, status="running")
        stage0_result = _run_stage_0(
            ticket_id=ticket_id,
            execution_id=execution_id,
            ticket_context=ticket_context,
            fields=fields,
        )
        _update_stage_status(execution_id, ticket_id, stage=0, status="completed")

        # ----------------------------------------------------------
        # Stage 1: Evaluation
        # ----------------------------------------------------------
        _update_stage_status(execution_id, ticket_id, stage=1, status="running")
        stage1_result = _run_stage_1(
            ticket_id=ticket_id,
            execution_id=execution_id,
            ticket_context=ticket_context,
            stage0_result=stage0_result,
            rules=rules,
            fields=fields,
        )
        _update_stage_status(execution_id, ticket_id, stage=1, status="completed")

        # ----------------------------------------------------------
        # Stage 2: Validation
        # ----------------------------------------------------------
        _update_stage_status(execution_id, ticket_id, stage=2, status="running")
        stage2_result = _run_stage_2(
            ticket_id=ticket_id,
            execution_id=execution_id,
            stage0_result=stage0_result,
            stage1_result=stage1_result,
            rules=rules,
            fields=fields,
        )
        _update_stage_status(execution_id, ticket_id, stage=2, status="completed")

        # ----------------------------------------------------------
        # Stage 3: Response generation (HITL cases only)
        # ----------------------------------------------------------
        stage3_result = None
        if stage2_result.get("requires_human_review"):
            _update_stage_status(execution_id, ticket_id, stage=3, status="running")
            stage3_result = _run_stage_3(
                ticket_id=ticket_id,
                execution_id=execution_id,
                stage0_result=stage0_result,
                stage1_result=stage1_result,
                stage2_result=stage2_result,
                fields=fields,
            )
            _update_stage_status(execution_id, ticket_id, stage=3, status="completed")

        # ----------------------------------------------------------
        # Write complaint record
        # ----------------------------------------------------------
        _write_complaint(
            ticket_id=ticket_id,
            execution_id=execution_id,
            fields=fields,
            stage0=stage0_result,
            stage1=stage1_result,
            stage2=stage2_result,
        )

        # ----------------------------------------------------------
        # Mark execution plan complete
        # ----------------------------------------------------------
        _complete_execution_plan(execution_id, status="completed")

        # ----------------------------------------------------------
        # ACK the stream message — processing confirmed complete
        # ----------------------------------------------------------
        _ack_message(stream_name, msg_id)

        final_action = stage2_result.get("final_action_code", "UNKNOWN")
        logger.info(
            "Task complete | execution_id=%s | ticket_id=%s | action=%s",
            execution_id, ticket_id, final_action,
        )

        return {
            "execution_id":    execution_id,
            "ticket_id":       ticket_id,
            "final_action_code": final_action,
            "status":          "completed",
        }

    except WorkerError as exc:
        logger.error(
            "WorkerError | execution_id=%s | ticket_id=%s | error=%s",
            execution_id, ticket_id, exc,
        )
        _handle_failure(execution_id, ticket_id, stream_name, msg_id, str(exc), self.request.retries)
        raise self.retry(exc=exc)

    except Exception as exc:
        logger.exception(
            "Unexpected error | execution_id=%s | ticket_id=%s | error=%s",
            execution_id, ticket_id, exc,
        )
        _handle_failure(execution_id, ticket_id, stream_name, msg_id, str(exc), self.request.retries)
        raise self.retry(exc=exc)


# ============================================================
# STAGE RUNNERS — STUBS
# Each stage is implemented in its own module under l4_agents/
# These stubs define the contract: inputs, output shape, DB writes.
# ============================================================

def _run_stage_0(
    ticket_id:      int,
    execution_id:   str,
    ticket_context: dict,
    fields:         dict,
) -> dict:
    """
    Stage 0: Classification
    Model: MODEL1 (gpt-4o-mini)

    Input:  ticket subject + description + order context
    Output: issue_type_l1, issue_type_l2, confidence, image_required
    Writes: llm_output_1 row

    Stub — replace with:
        from app.l4_agents.ecommerce.stage0_classifier import run
        return run(ticket_id, execution_id, ticket_context, fields)
    """
    logger.info("Stage 0 stub | ticket_id=%s | model=%s", ticket_id, MODEL_STAGE_0)

    result = {
        "llm_output_1_id": None,
        "issue_type_l1":   "delivery",
        "issue_type_l2":   "not_received",
        "confidence":      0.95,
        "image_required":  False,
        "reasoning":       "STUB — not yet implemented",
    }

    # Write stub row to llm_output_1
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.llm_output_1 (
                        ticket_id, execution_id, order_id,
                        issue_type_l1, issue_type_l2,
                        confidence_entailment, image_required,
                        reasoning, raw_response, status,
                        pipeline_status, execution_type
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 'in_progress', 'single')
                    RETURNING id
                    """,
                    (
                        ticket_id,
                        execution_id,
                        ticket_context.get("order_id"),
                        result["issue_type_l1"],
                        result["issue_type_l2"],
                        result["confidence"],
                        result["image_required"],
                        result["reasoning"],
                        "STUB",
                    ),
                )
                row = cur.fetchone()
                result["llm_output_1_id"] = row[0] if row else None
    finally:
        conn.close()

    return result


def _run_stage_1(
    ticket_id:      int,
    execution_id:   str,
    ticket_context: dict,
    stage0_result:  dict,
    rules:          list,
    fields:         dict,
) -> dict:
    """
    Stage 1: Evaluation
    Model: MODEL2 (gpt-4.1)

    Input:  stage0 classification + customer context + rules
    Output: action_code, calculated_gratification, fraud_segment, greedy_classification
    Writes: llm_output_2 row

    Stub — replace with:
        from app.l4_agents.ecommerce.stage1_evaluator import run
        return run(ticket_id, execution_id, ticket_context, stage0_result, rules, fields)
    """
    logger.info("Stage 1 stub | ticket_id=%s | model=%s", ticket_id, MODEL_STAGE_1)

    result = {
        "llm_output_2_id":        None,
        "action_code":            "REFUND_PARTIAL",
        "calculated_gratification": 50.0,
        "fraud_segment":          fields.get("fraud_risk_classification", "NORMAL"),
        "greedy_classification":  "not_greedy",
        "reasoning":              "STUB — not yet implemented",
    }

    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.llm_output_2 (
                        ticket_id, execution_id, order_id,
                        llm_output_1_id,
                        issue_type_l1_original, issue_type_l2_original,
                        fraud_segment, greedy_classification,
                        action_code, calculated_gratification
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        ticket_id,
                        execution_id,
                        ticket_context.get("order_id"),
                        stage0_result.get("llm_output_1_id"),
                        stage0_result.get("issue_type_l1"),
                        stage0_result.get("issue_type_l2"),
                        result["fraud_segment"],
                        result["greedy_classification"],
                        result["action_code"],
                        result["calculated_gratification"],
                    ),
                )
                row = cur.fetchone()
                result["llm_output_2_id"] = row[0] if row else None
    finally:
        conn.close()

    return result


def _run_stage_2(
    ticket_id:     int,
    execution_id:  str,
    stage0_result: dict,
    stage1_result: dict,
    rules:         list,
    fields:        dict,
) -> dict:
    """
    Stage 2: Validation
    Model: MODEL3 (o3-mini)

    Input:  stage1 evaluation + rules — validates logic, caps, fraud checks
    Output: final_action_code, final_refund_amount, validation_status, requires_human_review
    Writes: llm_output_3 row

    Stub — replace with:
        from app.l4_agents.ecommerce.stage2_validator import run
        return run(ticket_id, execution_id, stage0_result, stage1_result, rules, fields)
    """
    logger.info("Stage 2 stub | ticket_id=%s | model=%s", ticket_id, MODEL_STAGE_2)

    result = {
        "llm_output_3_id":        None,
        "final_action_code":      stage1_result.get("action_code", "REFUND_PARTIAL"),
        "final_refund_amount":    stage1_result.get("calculated_gratification", 0.0),
        "validation_status":      "PASSED",
        "requires_human_review":  False,
        "discrepancy_detected":   False,
        "reasoning":              "STUB — not yet implemented",
    }

    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.llm_output_3 (
                        ticket_id, execution_id, order_id,
                        llm_output_2_id,
                        final_action_code,
                        final_refund_amount,
                        logic_validation_status,
                        validated_calculated_gratification
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        ticket_id,
                        execution_id,
                        None,
                        stage1_result.get("llm_output_2_id"),
                        result["final_action_code"],
                        result["final_refund_amount"],
                        result["validation_status"],
                        result["final_refund_amount"],
                    ),
                )
                row = cur.fetchone()
                result["llm_output_3_id"] = row[0] if row else None
    finally:
        conn.close()

    return result


def _run_stage_3(
    ticket_id:     int,
    execution_id:  str,
    stage0_result: dict,
    stage1_result: dict,
    stage2_result: dict,
    fields:        dict,
) -> dict:
    """
    Stage 3: Response Generation
    Model: MODEL4 (gpt-4o)

    Only runs when Stage 2 sets requires_human_review=True.
    Generates the customer-facing response draft for the HITL agent.

    Stub — replace with:
        from app.l4_agents.ecommerce.stage3_responder import run
        return run(...)
    """
    logger.info("Stage 3 stub | ticket_id=%s | model=%s", ticket_id, MODEL_STAGE_3)
    return {
        "response_draft": "STUB — response generation not yet implemented",
        "hitl_queue":     fields.get("recommended_queue", "STANDARD_REVIEW"),
    }


# ============================================================
# CONTEXT + RULES FETCHERS
# ============================================================

def _fetch_ticket_context(ticket_id: int) -> Optional[dict]:
    """
    Fetch the full fdraw row including canonical_payload (with customer_context).
    Returns None if ticket not found.
    """
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    ticket_id, cx_email, subject, description,
                    order_id, module, source, thread_id,
                    canonical_payload, pipeline_stage
                FROM {SCHEMA}.fdraw
                WHERE ticket_id = %s
                LIMIT 1
                """,
                (ticket_id,),
            )
            row = cur.fetchone()
        return dict(row) if row else None
    except psycopg2.Error as exc:
        logger.error("Failed to fetch ticket context for ticket_id=%s: %s", ticket_id, exc)
        return None
    finally:
        conn.close()


def _fetch_rules(
    policy_version: str,
    module:         str,
    business_line:  str,
    fraud_segment:  str,
) -> list[dict]:
    """
    Fetch applicable rules from rule_registry for this ticket.
    Filters by policy_version + module_name + business_line.
    Returns rules ordered by priority ASC (lower number = higher priority).
    """
    if not policy_version or not module:
        logger.warning("Cannot fetch rules: policy_version or module missing")
        return []

    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    rule_id, rule_type, priority, rule_scope,
                    filters, numeric_constraints, flags,
                    conditions, action_id, action_payload,
                    issue_type_l1, issue_type_l2,
                    customer_segment, fraud_segment
                FROM {SCHEMA}.rule_registry
                WHERE policy_version = %s
                AND   module_name    = %s
                AND   (business_line = %s OR business_line IS NULL)
                ORDER BY priority ASC
                """,
                (policy_version, module, business_line),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    except psycopg2.Error as exc:
        logger.error("Failed to fetch rules: %s", exc)
        return []
    finally:
        conn.close()


# ============================================================
# STATE MANAGEMENT
# ============================================================

def _claim_ticket(execution_id: str, ticket_id: int) -> None:
    """
    Atomically mark this ticket as claimed by this worker.
    Updates ticket_processing_state and cardinal_execution_plans.
    """
    now = datetime.now(timezone.utc)
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.ticket_processing_state
                    SET
                        claimed_by           = %s,
                        claimed_at           = %s,
                        processing_started_at = %s
                    WHERE execution_id = %s
                    AND   ticket_id    = %s
                    """,
                    (CONSUMER_NAME, now, now, execution_id, ticket_id),
                )
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.cardinal_execution_plans
                    SET status     = 'processing',
                        started_at = %s
                    WHERE execution_id = %s
                    """,
                    (now, execution_id),
                )
    except psycopg2.Error as exc:
        logger.warning("Claim update failed for execution_id=%s: %s", execution_id, exc)
    finally:
        conn.close()


def _update_stage_status(
    execution_id: str,
    ticket_id:    int,
    stage:        int,
    status:       str,   # running | completed | failed
) -> None:
    """Update the stage_N_status column and current_stage in ticket_processing_state."""
    now = datetime.now(timezone.utc)
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                completed_col = f"stage_{stage}_completed_at"
                status_col    = f"stage_{stage}_status"

                if status == "completed":
                    cur.execute(
                        f"""
                        UPDATE {SCHEMA}.ticket_processing_state
                        SET {status_col} = %s,
                            {completed_col} = %s,
                            current_stage = %s
                        WHERE execution_id = %s AND ticket_id = %s
                        """,
                        (status, now, stage + 1, execution_id, ticket_id),
                    )
                else:
                    cur.execute(
                        f"""
                        UPDATE {SCHEMA}.ticket_processing_state
                        SET {status_col} = %s
                        WHERE execution_id = %s AND ticket_id = %s
                        """,
                        (status, execution_id, ticket_id),
                    )
    except psycopg2.Error as exc:
        logger.warning(
            "Stage status update failed | stage=%s | execution_id=%s: %s",
            stage, execution_id, exc,
        )
    finally:
        conn.close()


def _complete_execution_plan(execution_id: str, status: str = "completed") -> None:
    """Mark the execution plan as completed/failed with timestamp."""
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.cardinal_execution_plans
                    SET status       = %s,
                        completed_at = %s
                    WHERE execution_id = %s
                    """,
                    (status, datetime.now(timezone.utc), execution_id),
                )
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.ticket_processing_state
                    SET processing_completed_at = %s
                    WHERE execution_id = %s
                    """,
                    (datetime.now(timezone.utc), execution_id),
                )
    except psycopg2.Error as exc:
        logger.warning("Execution plan completion failed for %s: %s", execution_id, exc)
    finally:
        conn.close()


def _write_complaint(
    ticket_id:    int,
    execution_id: str,
    fields:       dict,
    stage0:       dict,
    stage1:       dict,
    stage2:       dict,
) -> None:
    """
    Write the complaints table row with the final resolution.
    This is the authoritative record of what was decided for this ticket.
    """
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.complaints (
                        ticket_id, execution_id,
                        customer_id, channel,
                        issue_type_l1, issue_type_l2,
                        escalation_group,
                        action_code, refund_amount,
                        resolution_status,
                        fraud_segment,
                        kb_version_used,
                        raised_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        ticket_id,
                        execution_id,
                        fields.get("customer_id") or None,
                        fields.get("channel", "email"),
                        stage0.get("issue_type_l1"),
                        stage0.get("issue_type_l2"),
                        fields.get("escalation_group", "STANDARD"),
                        stage2.get("final_action_code"),
                        stage2.get("final_refund_amount"),
                        "resolved" if not stage2.get("requires_human_review") else "pending_review",
                        stage1.get("fraud_segment", "NORMAL"),
                        fields.get("active_policy"),
                        datetime.now(timezone.utc),
                    ),
                )
    except psycopg2.Error as exc:
        logger.error("complaints write failed for ticket_id=%s: %s", ticket_id, exc)
    finally:
        conn.close()


def _handle_failure(
    execution_id: str,
    ticket_id:    int,
    stream_name:  str,
    msg_id:       str,
    error:        str,
    retry_count:  int,
) -> None:
    """
    On task failure: update ticket state with error message.
    On max retries exceeded: ACK the message to prevent infinite loop,
    mark execution as failed, write to dead_letter_store.
    """
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.ticket_processing_state
                    SET error_message = %s,
                        retry_count   = %s
                    WHERE execution_id = %s AND ticket_id = %s
                    """,
                    (error[:500], retry_count, execution_id, ticket_id),
                )
    except psycopg2.Error as exc:
        logger.warning("Failed to write error state: %s", exc)
    finally:
        conn.close()

    if retry_count >= MAX_RETRIES:
        logger.error(
            "Max retries exceeded | execution_id=%s | ticket_id=%s — "
            "ACKing and marking failed",
            execution_id, ticket_id,
        )
        _complete_execution_plan(execution_id, status="failed")
        _ack_message(stream_name, msg_id)   # ACK to stop redelivery


# ============================================================
# STREAM ACK
# ============================================================

def _ack_message(stream_name: str, msg_id: str) -> None:
    """
    Acknowledge a Redis Stream message — removes it from the
    consumer group's pending entries list (PEL).
    Called only after successful processing or on max retries exceeded.
    """
    try:
        r = get_redis()
        r.xack(stream_name, CONSUMER_GROUP, msg_id)
        logger.debug("ACK | stream=%s | msg_id=%s", stream_name, msg_id)
    except Exception as exc:
        logger.error("XACK failed for msg_id=%s: %s", msg_id, exc)


# ============================================================
# EXCEPTION
# ============================================================

class WorkerError(Exception):
    """Raised for recoverable worker-level errors that should trigger retry."""
    pass


# ============================================================
# WORKER ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    """
    Direct poll loop mode — alternative to Celery for single-process
    development and debugging.

    Usage:  python -m app.l4_agents.worker

    For production use Celery:
        celery -A app.l4_agents.worker.celery_app worker \
            --loglevel=info --concurrency=4 --queues=cardinal
    """
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Cardinal stream consumer | consumer=%s", CONSUMER_NAME)

    ensure_consumer_groups()

    last_reclaim = time.time()

    while True:
        dispatched = poll_streams_once()
        if dispatched:
            logger.info("Dispatched %d task(s) to Celery", dispatched)

        # Reclaim idle messages every 60 seconds
        if time.time() - last_reclaim > 60:
            reclaimed = reclaim_idle_messages()
            if reclaimed:
                logger.info("Reclaimed %d idle message(s)", reclaimed)
            last_reclaim = time.time()