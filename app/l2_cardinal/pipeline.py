"""
app/l2_cardinal/pipeline.py
==============================
Cardinal Pipeline — Orchestrator

Chains all five Cardinal phases into a single synchronous execution.
Called by the FastAPI router. Returns a typed response object that
the router serialises and returns to the caller.

Phase sequence:
    Phase 1 (validator)   → structural pre-checks, customer block check
    normaliser.run()      → write to fdraw, produce CanonicalPayload
    Phase 2 (deduplicator)→ hash check, Redis dedup window
    register_after_commit → Redis registration (after DB write confirmed)
    update_payload_hash   → patch hash back into fdraw.canonical_payload
    Phase 3 (handler)     → source verification, thread grouping, connector
    Phase 4 (enricher)    → customer profile, risk, order, policy version
    Phase 5 (dispatcher)  → execution_id, execution plan, Redis stream push

Error → HTTP status mapping:
    Phase 1 validation failure     → 422  VALIDATION_ERROR
    Phase 1 CUSTOMER_BLOCKED       → 403  CUSTOMER_BLOCKED
    normaliser failure             → 500  SYSTEM_ERROR
    Phase 2 duplicate              → 200  (DuplicateResponse, not an error)
    Phase 2 Redis outage           → 503  SYSTEM_ERROR
    Phase 3 source verify failure  → 401  SOURCE_VERIFICATION_FAILED
    Phase 3 DB error               → 500  SYSTEM_ERROR
    Phase 4 no active policy       → 503  POLICY_UNAVAILABLE
    Phase 5 Redis dispatch failure → 503  DISPATCH_FAILED
    Any unexpected exception       → 500  SYSTEM_ERROR

All responses use the typed schemas from l1_ingestion.schemas:
    202  IngestResponse
    200  DuplicateResponse
    4xx/5xx  ErrorResponse

The router maps these to the correct HTTP status codes.
pipeline.run() itself does not raise — it always returns a
PipelineResponse so the router has a single clean return path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Union

from app.l1_ingestion.schemas import (
    CanonicalPayload,
    CardinalIngestRequest,
    DuplicateResponse,
    ErrorResponse,
    IngestResponse,
)
from app.l1_ingestion import normaliser
from app.l2_cardinal import phase1_validator
from app.l2_cardinal import phase2_deduplicator
from app.l2_cardinal import phase3_handler
from app.l2_cardinal import phase4_enricher
from app.l2_cardinal import phase5_dispatcher
from app.l2_cardinal.phase2_deduplicator import DuplicateRequestError
from app.l2_cardinal.phase3_handler import SourceVerificationError, Phase3Error
from app.l2_cardinal.phase4_enricher import EnrichmentError
from app.l2_cardinal.phase5_dispatcher import DispatchError
from app.l1_ingestion.normaliser import NormaliserError

logger = logging.getLogger("cardinal.pipeline")


# ============================================================
# PIPELINE RESPONSE WRAPPER
# ============================================================

@dataclass
class PipelineResponse:
    """
    Wraps the typed response + HTTP status code.
    The router reads http_status and returns body as JSON.
    """
    http_status: int
    body: Union[IngestResponse, DuplicateResponse, ErrorResponse]


# ============================================================
# MAIN PUBLIC FUNCTION
# ============================================================

def run(
    request:    CardinalIngestRequest,
    raw_body:   Optional[bytes] = None,   # For Phase 3 Freshdesk HMAC check
    auth_token: Optional[str]  = None,    # For Phase 3 API token check
) -> PipelineResponse:
    """
    Execute the full Cardinal pipeline synchronously.

    Never raises — always returns a PipelineResponse.
    All exceptions are caught, logged, and mapped to error responses.

    Args:
        request:    Validated CardinalIngestRequest from the router.
        raw_body:   Raw HTTP request bytes — passed to Phase 3 for
                    Freshdesk signature verification.
        auth_token: Bearer token from Authorization header — passed
                    to Phase 3 for API token verification.

    Returns:
        PipelineResponse with http_status and a typed body.
    """
    ticket_id = None  # populated after normaliser writes to fdraw

    try:

        # ==============================================================
        # PHASE 1: Structural validation
        # ==============================================================
        logger.info(
            "Pipeline start | org=%s | source=%s | module=%s | channel=%s",
            request.org, request.source, request.module, request.channel,
        )

        validation_result = phase1_validator.run(request)

        if not validation_result.passed:
            # Check for CUSTOMER_BLOCKED specifically — different HTTP status
            blocked = next(
                (f for f in validation_result.failures
                 if f.error_code == "CUSTOMER_BLOCKED"),
                None,
            )
            if blocked:
                logger.warning(
                    "Pipeline rejected — customer blocked | org=%s | source=%s",
                    request.org, request.source,
                )
                return PipelineResponse(
                    http_status=403,
                    body=ErrorResponse(
                        error_code="CUSTOMER_BLOCKED",
                        message=blocked.message,
                        detail=None,
                    ),
                )

            # All other validation failures → 422
            failure_details = [
                {"check": f.check_name, "code": f.error_code, "message": f.message}
                for f in validation_result.failures
            ]
            logger.warning(
                "Pipeline rejected — validation failed | failures=%s",
                failure_details,
            )
            return PipelineResponse(
                http_status=422,
                body=ErrorResponse(
                    error_code="VALIDATION_ERROR",
                    message=(
                        f"{len(validation_result.failures)} validation check(s) failed."
                    ),
                    detail=failure_details,
                ),
            )

        _log_warnings("Phase 1", validation_result.warnings)

        # ==============================================================
        # NORMALISER: Write to fdraw, produce CanonicalPayload
        # ==============================================================
        canonical, ticket_id = normaliser.run(request)

        logger.info("Normaliser complete | ticket_id=%s", ticket_id)

        # ==============================================================
        # PHASE 2: Deduplication (check only — no Redis write yet)
        #
        # FIX: Pass request.payload (the raw inbound payload) instead of
        # canonical.model_dump(). The canonical object carries ticket_id
        # which is freshly assigned by the normaliser on every call —
        # this made every hash unique, breaking duplicate detection.
        # request.payload is identical for the same ticket regardless of
        # how many times it is submitted, which is the correct input for
        # an idempotency check.
        # ==============================================================
        payload_hash = phase2_deduplicator.run(
            canonical_payload=request.payload,  # ← was canonical.model_dump()
            ticket_id=ticket_id,
            source=request.source,
            customer_id=canonical.customer_id,
            channel=request.channel,
        )

        # Phase 2 passed — register in Redis AFTER fdraw write confirmed
        phase2_deduplicator.register_after_commit(payload_hash, ticket_id)

        # Patch hash back into fdraw.canonical_payload (non-fatal if fails)
        normaliser.update_payload_hash(ticket_id, payload_hash)
        canonical.payload_hash = payload_hash

        logger.info("Phase 2 complete | ticket_id=%s | hash=%s", ticket_id, payload_hash[:12])

        # ==============================================================
        # PHASE 3: Source verification, thread grouping, connector
        # ==============================================================
        phase3_result = phase3_handler.run(
            canonical=canonical,
            request=request,
            raw_body=raw_body,
            auth_token=auth_token,
        )
        _log_warnings("Phase 3", phase3_result.warnings)

        logger.info(
            "Phase 3 complete | ticket_id=%s | thread_id=%s | is_reply=%s",
            ticket_id,
            phase3_result.canonical.thread_id,
            phase3_result.is_thread_reply,
        )

        # ==============================================================
        # PHASE 4: Customer + order enrichment, policy resolution
        # ==============================================================
        phase4_result = phase4_enricher.run(phase3_result)
        _log_warnings("Phase 4", phase4_result.warnings)

        logger.info(
            "Phase 4 complete | ticket_id=%s | customer_id=%s | policy=%s",
            ticket_id,
            phase4_result.customer_id,
            phase4_result.active_policy,
        )

        # ==============================================================
        # PHASE 5: Dispatch to Redis Stream
        # ==============================================================
        phase5_result = phase5_dispatcher.run(phase4_result)
        _log_warnings("Phase 5", phase5_result.warnings)

        logger.info(
            "Pipeline complete | execution_id=%s | ticket_id=%s | "
            "priority=%s | stream=%s",
            phase5_result.execution_id,
            ticket_id,
            phase5_result.priority,
            phase5_result.stream_name,
        )

        # ==============================================================
        # SUCCESS — 202 Accepted
        # ==============================================================
        return PipelineResponse(
            http_status=202,
            body=IngestResponse(
                execution_id=phase5_result.execution_id,
                ticket_id=ticket_id,
                status="accepted",
                message=(
                    f"Ticket queued for processing. "
                    f"Priority: {phase5_result.priority}."
                ),
                is_sandbox=phase5_result.is_sandbox,
                received_at=datetime.now(timezone.utc),
            ),
        )

    # ==============================================================
    # EXCEPTION HANDLERS — in order of specificity
    # ==============================================================

    except DuplicateRequestError as exc:
        logger.info(
            "Duplicate payload detected | ticket_id=%s | original=%s | hash=%s",
            ticket_id, exc.original_ticket_id, exc.payload_hash[:12],
        )
        return PipelineResponse(
            http_status=200,
            body=DuplicateResponse(
                original_ticket_id=int(exc.original_ticket_id)
                    if exc.original_ticket_id else None,
                payload_hash=exc.payload_hash,
                message=(
                    f"Duplicate request. Original ticket: "
                    f"{exc.original_ticket_id or 'unknown'}."
                ),
            ),
        )

    except SourceVerificationError as exc:
        logger.warning("Source verification failed: %s", exc)
        return PipelineResponse(
            http_status=401,
            body=ErrorResponse(
                error_code="SOURCE_VERIFICATION_FAILED",
                message=str(exc),
                detail=None,
            ),
        )

    except EnrichmentError as exc:
        logger.error("Policy resolution failed: %s", exc)
        return PipelineResponse(
            http_status=503,
            body=ErrorResponse(
                error_code="POLICY_UNAVAILABLE",
                message=str(exc),
                detail=None,
            ),
        )

    except DispatchError as exc:
        logger.error("Redis dispatch failed: %s", exc)
        return PipelineResponse(
            http_status=503,
            body=ErrorResponse(
                error_code="DISPATCH_FAILED",
                message=(
                    "Ticket was ingested but could not be queued for processing. "
                    "Please retry. The original ticket has been preserved."
                ),
                detail=str(exc),
            ),
        )

    except NormaliserError as exc:
        logger.error("Normaliser failed: %s", exc)
        return PipelineResponse(
            http_status=500,
            body=ErrorResponse(
                error_code="SYSTEM_ERROR",
                message="Failed to write ticket to database.",
                detail=str(exc),
            ),
        )

    except Phase3Error as exc:
        logger.error("Phase 3 DB error: %s", exc)
        return PipelineResponse(
            http_status=500,
            body=ErrorResponse(
                error_code="SYSTEM_ERROR",
                message="Internal error during source handling.",
                detail=str(exc),
            ),
        )

    except Exception as exc:
        logger.exception(
            "Unexpected pipeline error | ticket_id=%s | error=%s",
            ticket_id, exc,
        )
        return PipelineResponse(
            http_status=500,
            body=ErrorResponse(
                error_code="SYSTEM_ERROR",
                message="An unexpected error occurred. The ticket may not have been processed.",
                detail=str(exc),
            ),
        )


# ============================================================
# HELPER
# ============================================================

def _log_warnings(phase: str, warnings: list[str]) -> None:
    for w in warnings:
        logger.warning("[%s] %s", phase, w)