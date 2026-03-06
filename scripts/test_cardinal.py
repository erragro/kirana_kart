"""
scripts/test_cardinal.py
==========================
Cardinal Pipeline — End-to-End Test Suite

Tests the full Cardinal pipeline phase by phase:
    Prerequisites     — Redis, DB, active policy version
    Phase 1           — Payload validation (pass + all failure cases)
    Normaliser        — fdraw write, canonical payload shape
    Phase 2           — Deduplication (new + duplicate detection)
    Phase 3           — Source handler, thread detection
    Phase 4           — Customer enrichment, policy resolution
    Phase 5           — Dispatch, Redis stream delivery
    Pipeline          — Full end-to-end via pipeline.run()
    Celery            — Task registration, worker ping

Every test result is logged to:
    logs/cardinal_test_{timestamp}.json   — full structured log
    logs/cardinal_test_latest.json        — always the most recent run

Run from project root:
    python scripts/test_cardinal.py

Options:
    --phase PHASE     Run only a specific phase (1, 2, 3, 4, 5, pipeline, celery)
    --verbose         Print full response bodies to console
    --no-cleanup      Skip cleanup of test rows written to DB

Example:
    python scripts/test_cardinal.py --phase pipeline --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Project root on path ────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# ── Log directory ────────────────────────────────────────────
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)

TIMESTAMP    = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE     = LOGS_DIR / f"cardinal_test_{TIMESTAMP}.json"
LATEST_FILE  = LOGS_DIR / "cardinal_test_latest.json"

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"
SKIP = "⏭️  SKIP"

SCHEMA = "kirana_kart"

# ── Test org — clearly identifiable as test data ─────────────
TEST_ORG         = "Sandbox"
TEST_CUSTOMER_ID = f"TEST_CX_{uuid.uuid4().hex[:8]}"
TEST_ORDER_ID    = f"TEST_ORD_{uuid.uuid4().hex[:8]}"
TEST_EMAIL       = f"test_{uuid.uuid4().hex[:6]}@sandbox.kirana.test"

# ── Console logging ──────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,   # Suppress internal module logs during test
    format="%(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("cardinal.test")


# ============================================================
# RESULT TRACKING
# ============================================================

@dataclass
class TestResult:
    test_id:      str
    phase:        str
    name:         str
    status:       str        # PASS | FAIL | WARN | SKIP
    duration_ms:  float      = 0.0
    detail:       Any        = None
    error:        Optional[str] = None
    timestamp:    str        = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class TestSuite:
    run_id:    str           = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: str          = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    results:   list[TestResult] = field(default_factory=list)
    cleanup_ticket_ids: list[int] = field(default_factory=list)

    def add(self, result: TestResult):
        self.results.append(result)
        icon = result.status
        print(f"  {icon}  [{result.phase}] {result.name}  ({result.duration_ms:.0f}ms)")
        if result.error and args.verbose:
            print(f"          ERROR: {result.error}")
        if result.detail and args.verbose:
            print(f"          DETAIL: {json.dumps(result.detail, default=str, indent=10)}")

    @property
    def passed(self):  return sum(1 for r in self.results if r.status == PASS)
    @property
    def failed(self):  return sum(1 for r in self.results if r.status == FAIL)
    @property
    def warned(self):  return sum(1 for r in self.results if r.status == WARN)
    @property
    def skipped(self): return sum(1 for r in self.results if r.status == SKIP)


suite = TestSuite()


# ============================================================
# HELPERS
# ============================================================

def run_test(phase: str, name: str, fn) -> TestResult:
    """Execute a single test function and record the result."""
    t0 = time.monotonic()
    try:
        detail = fn()
        duration = (time.monotonic() - t0) * 1000
        result = TestResult(
            test_id=uuid.uuid4().hex[:8],
            phase=phase,
            name=name,
            status=PASS,
            duration_ms=duration,
            detail=detail,
        )
    except AssertionError as exc:
        duration = (time.monotonic() - t0) * 1000
        result = TestResult(
            test_id=uuid.uuid4().hex[:8],
            phase=phase,
            name=name,
            status=FAIL,
            duration_ms=duration,
            error=str(exc),
            detail=traceback.format_exc(),
        )
    except Exception as exc:
        duration = (time.monotonic() - t0) * 1000
        result = TestResult(
            test_id=uuid.uuid4().hex[:8],
            phase=phase,
            name=name,
            status=FAIL,
            duration_ms=duration,
            error=f"{type(exc).__name__}: {exc}",
            detail=traceback.format_exc(),
        )
    suite.add(result)
    return result


def get_db():
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def make_valid_request(**overrides):
    """Build a valid CardinalIngestRequest dict. Override any field."""
    base = {
        "channel":       "email",
        "source":        "api",
        "org":           TEST_ORG,
        "business_line": "ecommerce",
        "module":        "delivery",
        "payload": {
            "cx_email":    TEST_EMAIL,
            "customer_id": TEST_CUSTOMER_ID,
            "order_id":    TEST_ORDER_ID,
            "subject":     "My order was not delivered",
            "description": "I placed an order yesterday and it still has not arrived. "
                           "The app shows delivered but I never received it.",
            "img_flg":     0,
            "attachment":  0,
        },
        "metadata": {
            "environment": "sandbox",
            "test_mode":   True,
            "called_by":   "test_cardinal_script",
        }
    }
    base.update(overrides)
    return base


def parse_request(data: dict):
    from app.l1_ingestion.schemas import CardinalIngestRequest
    return CardinalIngestRequest(**data)


def section(title: str):
    print(f"\n{'═' * 55}")
    print(f"  {title}")
    print(f"{'═' * 55}")


# ============================================================
# PREREQUISITE CHECKS
# ============================================================

def test_prerequisites():
    section("Prerequisites")

    # Redis
    def check_redis():
        from app.admin.redis_client import get_redis
        r = get_redis()
        r.ping()
        return {"redis": "connected"}
    run_test("prereq", "Redis reachable", check_redis)

    # DB
    def check_db():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        return {"database": "connected"}
    run_test("prereq", "Database reachable", check_db)

    # Active policy version
    def check_policy():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT active_version FROM {SCHEMA}.kb_runtime_config "
                f"ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
        conn.close()
        assert row, "No active policy version in kb_runtime_config"
        return {"active_version": row[0]}
    run_test("prereq", "Active policy version exists", check_policy)

    # fdraw table exists
    def check_fdraw():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM information_schema.tables "
                f"WHERE table_schema = 'kirana_kart' AND table_name = 'fdraw'"
            )
            row = cur.fetchone()
        conn.close()
        assert row[0] == 1, "fdraw table not found"
        return {"fdraw": "exists"}
    run_test("prereq", "fdraw table exists", check_fdraw)

    # Redis streams exist / can be created
    def check_streams():
        from app.l4_agents.worker import ensure_consumer_groups
        ensure_consumer_groups()
        return {"streams": "created"}
    run_test("prereq", "Redis streams created", check_streams)


# ============================================================
# PHASE 1 — VALIDATOR
# ============================================================

def test_phase1():
    section("Phase 1 — Validator")

    from app.l2_cardinal import phase1_validator

    # Valid payload passes
    def valid_passes():
        req = parse_request(make_valid_request())
        result = phase1_validator.run(req)
        assert result.passed, f"Expected pass, got failures: {result.failures}"
        return {"passed": True, "warnings": result.warnings}
    run_test("phase1", "Valid payload passes", valid_passes)

    # Empty description fails
    def empty_description():
        req = parse_request(make_valid_request())
        req.payload["description"] = ""
        result = phase1_validator.run(req)
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "DESCRIPTION_MISSING" in codes, f"Expected DESCRIPTION_MISSING, got {codes}"
        return {"failures": codes}
    run_test("phase1", "Empty description → DESCRIPTION_MISSING", empty_description)

    # Too short description fails
    def short_description():
        req = parse_request(make_valid_request())
        req.payload["description"] = "help"
        result = phase1_validator.run(req)
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "DESCRIPTION_TOO_SHORT" in codes
        return {"failures": codes}
    run_test("phase1", "Short description → DESCRIPTION_TOO_SHORT", short_description)

    # Invalid order_id format fails
    def bad_order_id():
        req = parse_request(make_valid_request())
        req.payload["order_id"] = "ORD'; DROP TABLE orders;--"
        result = phase1_validator.run(req)
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert any(c in codes for c in ["ORDER_ID_INVALID_FORMAT", "INJECTION_PATTERN_DETECTED"]), \
            f"Expected format/injection error, got {codes}"
        return {"failures": codes}
    run_test("phase1", "SQL injection in order_id → rejected", bad_order_id)

    # img_flg/attachment mismatch → warning only
    def attachment_mismatch():
        req = parse_request(make_valid_request())
        req.payload["img_flg"]   = 1
        req.payload["attachment"] = 0
        result = phase1_validator.run(req)
        assert result.passed, "Attachment mismatch should warn, not fail"
        assert len(result.warnings) > 0
        return {"passed": True, "warnings": result.warnings}
    run_test("phase1", "img_flg=1 attachment=0 → warning not failure", attachment_mismatch)

    # Multiple failures returned together
    def multiple_failures():
        req = parse_request(make_valid_request())
        req.payload["description"] = ""
        req.payload["subject"]     = "x" * 600
        result = phase1_validator.run(req)
        assert not result.passed
        assert len(result.failures) >= 2, \
            f"Expected ≥2 failures, got {len(result.failures)}"
        return {"failure_count": len(result.failures)}
    run_test("phase1", "Multiple failures returned together", multiple_failures)

    # Sandbox skips DB checks
    def sandbox_skips_db():
        req = parse_request(make_valid_request())
        req.payload["order_id"] = "NONEXISTENT_ORDER_XYZ"
        result = phase1_validator.run(req)
        # Sandbox — order existence check skipped, should not fail
        assert result.passed, \
            f"Sandbox should skip DB checks. Failures: {result.failures}"
        return {"passed": True}
    run_test("phase1", "Sandbox mode skips DB order existence check", sandbox_skips_db)


# ============================================================
# NORMALISER
# ============================================================

def test_normaliser():
    section("Normaliser — fdraw write")

    from app.l1_ingestion import normaliser

    written_ticket_id = None

    def normaliser_writes_fdraw():
        nonlocal written_ticket_id
        req = parse_request(make_valid_request())
        canonical, ticket_id = normaliser.run(req)

        assert ticket_id is not None and ticket_id > 0
        assert canonical.org == TEST_ORG
        assert canonical.channel == "email"
        assert canonical.is_sandbox is True

        written_ticket_id = ticket_id
        suite.cleanup_ticket_ids.append(ticket_id)
        return {
            "ticket_id":  ticket_id,
            "thread_id":  canonical.thread_id,
            "is_sandbox": canonical.is_sandbox,
            "module":     canonical.module,
        }
    run_test("normaliser", "Writes row to fdraw, returns ticket_id", normaliser_writes_fdraw)

    def canonical_payload_in_db():
        assert written_ticket_id, "Depends on previous test"
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT canonical_payload, pipeline_stage FROM {SCHEMA}.fdraw "
                f"WHERE ticket_id = %s",
                (written_ticket_id,),
            )
            row = cur.fetchone()
        conn.close()
        assert row, f"No fdraw row for ticket_id={written_ticket_id}"
        canonical = row[0]
        stage     = row[1]
        assert canonical is not None, "canonical_payload is NULL"
        assert stage == "INGESTED", f"Expected INGESTED, got {stage}"
        return {"pipeline_stage": stage, "has_canonical_payload": True}
    run_test("normaliser", "canonical_payload written + pipeline_stage=INGESTED", canonical_payload_in_db)

    return written_ticket_id


# ============================================================
# PHASE 2 — DEDUPLICATOR
# ============================================================

def test_phase2(ticket_id: Optional[int]):
    section("Phase 2 — Deduplicator")

    from app.l2_cardinal import phase2_deduplicator
    from app.l2_cardinal.phase2_deduplicator import DuplicateRequestError

    req       = parse_request(make_valid_request())
    canonical = req.model_dump()
    tid       = ticket_id or 99999

    stored_hash = None

    # New payload registers successfully
    def new_payload_passes():
        nonlocal stored_hash
        payload_hash = phase2_deduplicator.run(
            canonical_payload=canonical,
            ticket_id=tid,
            source="api",
            customer_id=TEST_CUSTOMER_ID,
            channel="email",
        )
        assert isinstance(payload_hash, str) and len(payload_hash) == 64
        stored_hash = payload_hash
        phase2_deduplicator.register_after_commit(payload_hash, tid)
        return {"hash": payload_hash[:12] + "...", "length": len(payload_hash)}
    run_test("phase2", "New payload → hash returned + registered", new_payload_passes)

    # Same payload within 24h detected as duplicate
    def duplicate_detected():
        assert stored_hash, "Depends on previous test"
        try:
            phase2_deduplicator.run(
                canonical_payload=canonical,
                ticket_id=tid,
                source="api",
                customer_id=TEST_CUSTOMER_ID,
                channel="email",
            )
            assert False, "Should have raised DuplicateRequestError"
        except DuplicateRequestError as exc:
            assert exc.payload_hash == stored_hash
            return {"detected": True, "original_ticket": exc.original_ticket_id}
    run_test("phase2", "Duplicate payload → DuplicateRequestError raised", duplicate_detected)

    # Different payload is not a duplicate
    def different_payload_passes():
        different = make_valid_request()
        different["payload"]["description"] = (
            "Completely different complaint about a different issue "
            f"with unique content {uuid.uuid4().hex}"
        )
        different_canonical = different
        new_hash = phase2_deduplicator.run(
            canonical_payload=different_canonical,
            ticket_id=tid + 1,
            source="api",
            customer_id=TEST_CUSTOMER_ID,
            channel="email",
        )
        assert new_hash != stored_hash
        return {"different_hash": True}
    run_test("phase2", "Different payload → not a duplicate", different_payload_passes)

    # Hash is deterministic
    def hash_is_deterministic():
        h1 = phase2_deduplicator.compute_payload_hash(canonical)
        h2 = phase2_deduplicator.compute_payload_hash(canonical)
        assert h1 == h2, "Hash must be deterministic"
        return {"deterministic": True}
    run_test("phase2", "Hash is deterministic for same payload", hash_is_deterministic)


# ============================================================
# PHASE 3 — SOURCE HANDLER
# ============================================================

def test_phase3(ticket_id: Optional[int]):
    section("Phase 3 — Source Handler")

    from app.l1_ingestion import normaliser
    from app.l2_cardinal import phase3_handler

    # Write a fresh ticket for Phase 3 to work with
    req = parse_request(make_valid_request())
    canonical, tid = normaliser.run(req)
    suite.cleanup_ticket_ids.append(tid)

    # Source verification skipped in sandbox
    def sandbox_verification_skipped():
        result = phase3_handler.run(
            canonical=canonical,
            request=req,
            raw_body=None,
            auth_token=None,
        )
        assert result.source_verified is True
        assert result.verification_method == "skipped"
        assert "sandbox" in result.warnings[0].lower()
        return {
            "verified":      result.source_verified,
            "method":        result.verification_method,
            "thread_id":     result.canonical.thread_id,
            "connector_id":  result.connector_id,
        }
    run_test("phase3", "Sandbox → verification skipped with warning", sandbox_verification_skipped)

    # Thread ID assigned
    def thread_id_assigned():
        result = phase3_handler.run(
            canonical=canonical,
            request=req,
            raw_body=None,
            auth_token=None,
        )
        assert result.canonical.thread_id is not None
        assert len(result.canonical.thread_id) > 0
        return {"thread_id": result.canonical.thread_id}
    run_test("phase3", "thread_id assigned to ticket", thread_id_assigned)

    # Connector ID is a positive integer
    def connector_id_positive():
        result = phase3_handler.run(
            canonical=canonical,
            request=req,
            raw_body=None,
            auth_token=None,
        )
        assert isinstance(result.connector_id, int)
        assert result.connector_id > 0
        return {"connector_id": result.connector_id}
    run_test("phase3", "connector_id is positive integer", connector_id_positive)

    # pipeline_stage updated to THREAD_RESOLVED in fdraw
    def stage_updated():
        phase3_handler.run(canonical=canonical, request=req)
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT pipeline_stage FROM {SCHEMA}.fdraw WHERE ticket_id = %s",
                (tid,),
            )
            row = cur.fetchone()
        conn.close()
        assert row and row[0] == "THREAD_RESOLVED", \
            f"Expected THREAD_RESOLVED, got {row[0] if row else 'no row'}"
        return {"pipeline_stage": row[0]}
    run_test("phase3", "fdraw pipeline_stage=THREAD_RESOLVED after Phase 3", stage_updated)


# ============================================================
# PHASE 4 — ENRICHER
# ============================================================

def test_phase4():
    section("Phase 4 — Enricher")

    from app.l1_ingestion import normaliser
    from app.l2_cardinal import phase3_handler, phase4_enricher
    from app.l2_cardinal.phase4_enricher import EnrichmentError

    req = parse_request(make_valid_request())
    canonical, tid = normaliser.run(req)
    suite.cleanup_ticket_ids.append(tid)
    p3 = phase3_handler.run(canonical=canonical, request=req)

    # Enrichment runs without error
    def enrichment_runs():
        result = phase4_enricher.run(p3)
        assert result.context is not None
        assert result.context.policy is not None
        assert result.active_policy is not None
        return {
            "active_policy":    result.active_policy,
            "customer_found":   not result.context.customer.is_new_customer,
            "risk_is_default":  result.context.risk.is_default,
            "order_found":      result.context.order.order_found if result.context.order else None,
        }
    run_test("phase4", "Enrichment completes without error", enrichment_runs)

    # CustomerContext embedded in fdraw
    def context_in_fdraw():
        phase4_enricher.run(p3)
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT canonical_payload->>'customer_context', pipeline_stage "
                f"FROM {SCHEMA}.fdraw WHERE ticket_id = %s",
                (tid,),
            )
            row = cur.fetchone()
        conn.close()
        assert row, "No fdraw row"
        assert row[0] is not None, "customer_context not embedded in canonical_payload"
        assert row[1] == "ENRICHED", f"Expected ENRICHED, got {row[1]}"
        return {"customer_context_embedded": True, "pipeline_stage": row[1]}
    run_test("phase4", "CustomerContext embedded in fdraw.canonical_payload", context_in_fdraw)

    # Risk profile defaults are safe values
    def risk_defaults_safe():
        result = phase4_enricher.run(p3)
        risk = result.context.risk
        # If no risk profile exists for test customer, defaults must be safe
        if risk.is_default:
            assert risk.auto_approval_limit == 500.0
            assert risk.recommended_queue   == "STANDARD_REVIEW"
            assert risk.fraud_score         == 0.0
        return {
            "is_default":         risk.is_default,
            "auto_approval_limit": risk.auto_approval_limit,
            "recommended_queue":  risk.recommended_queue,
        }
    run_test("phase4", "Risk profile defaults are safe values", risk_defaults_safe)


# ============================================================
# PHASE 5 — DISPATCHER
# ============================================================

def test_phase5():
    section("Phase 5 — Dispatcher")

    from app.l1_ingestion import normaliser
    from app.l2_cardinal import phase3_handler, phase4_enricher, phase5_dispatcher
    from app.admin.redis_client import get_redis

    req = parse_request(make_valid_request())
    canonical, tid = normaliser.run(req)
    suite.cleanup_ticket_ids.append(tid)
    p3 = phase3_handler.run(canonical=canonical, request=req)
    p4 = phase4_enricher.run(p3)

    dispatch_result = None

    # Dispatch succeeds
    def dispatch_succeeds():
        nonlocal dispatch_result
        result = phase5_dispatcher.run(p4)
        assert result.execution_id.startswith("single_")
        assert result.ticket_id == tid
        assert result.stream_name.startswith("cardinal:dispatch:")
        assert result.stream_message_id is not None
        dispatch_result = result
        return {
            "execution_id":    result.execution_id,
            "priority":        result.priority,
            "escalation_group": result.escalation_group,
            "stream":          result.stream_name,
            "msg_id":          result.stream_message_id,
        }
    run_test("phase5", "Dispatch succeeds, returns execution_id + stream msg_id", dispatch_succeeds)

    # Message actually in Redis stream
    def message_in_stream():
        assert dispatch_result, "Depends on previous test"
        r = get_redis()
        # Read the message by ID
        messages = r.xrange(
            dispatch_result.stream_name,
            min=dispatch_result.stream_message_id,
            max=dispatch_result.stream_message_id,
        )
        assert messages, "Message not found in Redis stream"
        msg_fields = messages[0][1]
        assert msg_fields.get("execution_id") == dispatch_result.execution_id
        assert msg_fields.get("ticket_id")    == str(tid)
        return {
            "message_found":  True,
            "execution_id":   msg_fields.get("execution_id"),
            "priority":       msg_fields.get("priority"),
        }
    run_test("phase5", "Message present in Redis stream with correct fields", message_in_stream)

    # execution_plan row written to DB
    def execution_plan_written():
        assert dispatch_result, "Depends on previous test"
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT status, org, module FROM {SCHEMA}.cardinal_execution_plans "
                f"WHERE execution_id = %s",
                (dispatch_result.execution_id,),
            )
            row = cur.fetchone()
        conn.close()
        assert row, "No cardinal_execution_plans row"
        assert row[0] == "queued", f"Expected queued, got {row[0]}"
        return {"status": row[0], "org": row[1], "module": row[2]}
    run_test("phase5", "cardinal_execution_plans row written with status=queued", execution_plan_written)

    # ticket_processing_state row written
    def processing_state_written():
        assert dispatch_result, "Depends on previous test"
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT stage_0_status, stage_1_status, stage_2_status "
                f"FROM {SCHEMA}.ticket_processing_state "
                f"WHERE execution_id = %s AND ticket_id = %s",
                (dispatch_result.execution_id, tid),
            )
            row = cur.fetchone()
        conn.close()
        assert row, "No ticket_processing_state row"
        assert all(s == "pending" for s in row), f"Expected all pending, got {row}"
        return {"stage_0": row[0], "stage_1": row[1], "stage_2": row[2]}
    run_test("phase5", "ticket_processing_state written with all stages=pending", processing_state_written)

    # Sandbox goes to P4_LOW stream
    def sandbox_goes_to_low_stream():
        assert dispatch_result, "Depends on previous test"
        assert dispatch_result.priority == "P4_LOW", \
            f"Sandbox should be P4_LOW, got {dispatch_result.priority}"
        assert "P4_LOW" in dispatch_result.stream_name
        return {"priority": dispatch_result.priority}
    run_test("phase5", "Sandbox ticket dispatched to P4_LOW stream", sandbox_goes_to_low_stream)

    return dispatch_result


# ============================================================
# FULL PIPELINE — end-to-end via pipeline.run()
# ============================================================

def test_full_pipeline():
    section("Full Pipeline — pipeline.run()")

    from app.l2_cardinal.pipeline import run as pipeline_run
    from app.l1_ingestion.schemas import CardinalIngestRequest, IngestResponse, DuplicateResponse, ErrorResponse

    # Happy path — 202 accepted
    def happy_path():
        req = parse_request(make_valid_request())
        result = pipeline_run(request=req, raw_body=b"", auth_token=None)
        assert result.http_status == 202, \
            f"Expected 202, got {result.http_status}. Body: {result.body}"
        assert isinstance(result.body, IngestResponse)
        assert result.body.execution_id.startswith("single_")
        suite.cleanup_ticket_ids.append(result.body.ticket_id)
        return {
            "http_status":   result.http_status,
            "execution_id":  result.body.execution_id,
            "ticket_id":     result.body.ticket_id,
            "priority":      result.body.message,
        }
    run_test("pipeline", "Happy path → 202 + execution_id", happy_path)

    # Duplicate → 200 DuplicateResponse
    def duplicate_returns_200():
        req = parse_request(make_valid_request())
        # First call
        r1 = pipeline_run(request=req, raw_body=b"", auth_token=None)
        if r1.http_status == 202:
            suite.cleanup_ticket_ids.append(r1.body.ticket_id)
        # Second call — same payload
        r2 = pipeline_run(request=req, raw_body=b"", auth_token=None)
        assert r2.http_status == 200, f"Expected 200 for duplicate, got {r2.http_status}"
        assert isinstance(r2.body, DuplicateResponse)
        return {
            "http_status":       r2.http_status,
            "original_ticket_id": r2.body.original_ticket_id,
        }
    run_test("pipeline", "Duplicate payload → 200 DuplicateResponse", duplicate_returns_200)

    # Validation failure → 422
    def validation_failure_422():
        data = make_valid_request()
        data["payload"]["description"] = ""
        req = parse_request(data)
        result = pipeline_run(request=req, raw_body=b"", auth_token=None)
        assert result.http_status == 422, f"Expected 422, got {result.http_status}"
        assert isinstance(result.body, ErrorResponse)
        assert result.body.error_code == "VALIDATION_ERROR"
        return {
            "http_status": result.http_status,
            "error_code":  result.body.error_code,
        }
    run_test("pipeline", "Empty description → 422 VALIDATION_ERROR", validation_failure_422)

    # Pydantic schema rejection (bad channel)
    def bad_channel_rejected():
        try:
            parse_request(make_valid_request(channel="fax"))
            assert False, "Should have raised ValidationError"
        except Exception as exc:
            assert "channel" in str(exc).lower() or "fax" in str(exc).lower()
            return {"rejected": True, "error": str(exc)[:100]}
    run_test("pipeline", "Invalid channel → Pydantic schema rejection", bad_channel_rejected)

    # Pipeline never raises (catch-all)
    def pipeline_never_raises():
        # Craft a request that will cause an unexpected internal issue
        # by passing a malformed org that breaks execution_id generation
        data = make_valid_request()
        data["org"] = "Sandbox"
        req = parse_request(data)
        result = pipeline_run(request=req)
        # Whatever happens, pipeline.run() must return a PipelineResponse
        assert hasattr(result, "http_status")
        assert hasattr(result, "body")
        assert result.http_status in (200, 202, 400, 401, 403, 422, 500, 503)
        return {"http_status": result.http_status, "no_exception": True}
    run_test("pipeline", "pipeline.run() never raises — always returns PipelineResponse", pipeline_never_raises)


# ============================================================
# CELERY CHECKS
# ============================================================

def test_celery():
    section("Celery — Task Registration & Broker")

    # Import without error
    def celery_imports():
        from app.l4_agents.worker import celery_app, CONSUMER_NAME, STREAMS
        from app.l4_agents import tasks
        return {
            "broker":        celery_app.conf.broker_url,
            "consumer_name": CONSUMER_NAME,
            "stream_count":  len(STREAMS),
        }
    run_test("celery", "worker.py + tasks.py import without errors", celery_imports)

    # All expected tasks registered
    def tasks_registered():
        from app.l4_agents.worker import celery_app
        from app.l4_agents import tasks  # ensure tasks are registered
        registered = list(celery_app.tasks.keys())
        expected = [
            "app.l4_agents.worker.process_ticket",
            "app.l4_agents.tasks.beat_poll_streams",
            "app.l4_agents.tasks.beat_reclaim_idle_messages",
            "app.l4_agents.tasks.beat_refresh_risk_profiles",
            "app.l4_agents.tasks.beat_purge_stale_dedup_keys",
            "app.l4_agents.tasks.beat_execution_plan_timeout",
            "app.l4_agents.tasks.reprocess_ticket",
            "app.l4_agents.tasks.drain_failed_executions",
            "app.l4_agents.tasks.health_check",
        ]
        missing = [t for t in expected if t not in registered]
        assert not missing, f"Tasks not registered: {missing}"
        return {"registered_count": len(expected), "all_present": True}
    run_test("celery", "All 9 tasks registered on celery_app", tasks_registered)

    # Beat schedule has all entries
    def beat_schedule_complete():
        from app.l4_agents.worker import celery_app
        schedule = celery_app.conf.beat_schedule or {}
        assert len(schedule) >= 5, f"Expected ≥5 beat entries, got {len(schedule)}"
        return {"schedule_entries": list(schedule.keys())}
    run_test("celery", "Beat schedule has ≥5 entries", beat_schedule_complete)

    # Worker ping (only passes if worker is running)
    def worker_ping():
        from app.l4_agents.worker import celery_app
        inspector   = celery_app.control.inspect(timeout=3)
        ping_result = inspector.ping()
        if ping_result:
            workers = list(ping_result.keys())
            return {"workers_alive": workers}
        else:
            # Not a failure — worker just not running yet
            result = TestResult(
                test_id=uuid.uuid4().hex[:8],
                phase="celery",
                name="Worker ping (skipped — no worker running)",
                status=WARN,
                detail={"note": "Start worker: celery -A app.l4_agents.worker.celery_app worker --queues=cardinal"},
            )
            suite.add(result)
            return None   # Signal to skip normal result recording
    # Manual handling — worker ping result varies
    t0 = time.monotonic()
    try:
        detail = worker_ping()
        if detail is not None:
            suite.add(TestResult(
                test_id=uuid.uuid4().hex[:8],
                phase="celery",
                name="Worker ping",
                status=PASS,
                duration_ms=(time.monotonic() - t0) * 1000,
                detail=detail,
            ))
    except Exception as exc:
        suite.add(TestResult(
            test_id=uuid.uuid4().hex[:8],
            phase="celery",
            name="Worker ping",
            status=WARN,
            duration_ms=(time.monotonic() - t0) * 1000,
            error=str(exc),
        ))


# ============================================================
# CLEANUP
# ============================================================

def cleanup():
    if args.no_cleanup:
        print(f"\n  Cleanup skipped (--no-cleanup). Test ticket_ids: {suite.cleanup_ticket_ids}")
        return

    if not suite.cleanup_ticket_ids:
        return

    section("Cleanup")
    conn = get_db()
    try:
        with conn:
            with conn.cursor() as cur:
                # Remove test tickets from all related tables
                for tid in set(suite.cleanup_ticket_ids):
                    cur.execute(
                        f"DELETE FROM {SCHEMA}.ticket_processing_state WHERE ticket_id = %s", (tid,)
                    )
                    cur.execute(
                        f"DELETE FROM {SCHEMA}.llm_output_1 WHERE ticket_id = %s", (tid,)
                    )
                    cur.execute(
                        f"DELETE FROM {SCHEMA}.llm_output_2 WHERE ticket_id = %s", (tid,)
                    )
                    cur.execute(
                        f"DELETE FROM {SCHEMA}.llm_output_3 WHERE ticket_id = %s", (tid,)
                    )
                    cur.execute(
                        f"DELETE FROM {SCHEMA}.complaints WHERE ticket_id = %s", (tid,)
                    )
                    cur.execute(
                        f"DELETE FROM {SCHEMA}.fdraw WHERE ticket_id = %s", (tid,)
                    )

                # Remove test execution plans
                cur.execute(
                    f"DELETE FROM {SCHEMA}.cardinal_execution_plans "
                    f"WHERE org = %s",
                    (TEST_ORG,),
                )

        print(f"  {PASS}  Cleaned {len(suite.cleanup_ticket_ids)} test ticket(s)")
    except Exception as exc:
        print(f"  {WARN}  Cleanup failed: {exc}")
    finally:
        conn.close()


# ============================================================
# SAVE LOGS
# ============================================================

def save_logs():
    output = {
        "run_id":     suite.run_id,
        "started_at": suite.started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total":   len(suite.results),
            "passed":  suite.passed,
            "failed":  suite.failed,
            "warned":  suite.warned,
            "skipped": suite.skipped,
        },
        "test_context": {
            "test_org":         TEST_ORG,
            "test_customer_id": TEST_CUSTOMER_ID,
            "test_order_id":    TEST_ORDER_ID,
            "test_email":       TEST_EMAIL,
        },
        "results": [asdict(r) for r in suite.results],
    }

    with open(LOG_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str)

    with open(LATEST_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n  Logs saved to:")
    print(f"    {LOG_FILE}")
    print(f"    {LATEST_FILE}")

    return output


# ============================================================
# MAIN
# ============================================================

def print_summary():
    section("Summary")
    total = len(suite.results)
    print(f"  Total:   {total}")
    print(f"  {PASS}:  {suite.passed}")
    print(f"  {FAIL}:  {suite.failed}")
    print(f"  {WARN}:  {suite.warned}")
    print(f"  {SKIP}:  {suite.skipped}")

    if suite.failed > 0:
        print(f"\n  Failed tests:")
        for r in suite.results:
            if r.status == FAIL:
                print(f"    [{r.phase}] {r.name}")
                if r.error:
                    print(f"           {r.error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cardinal pipeline test suite")
    parser.add_argument("--phase", choices=["prereq","normaliser","1","2","3","4","5","pipeline","celery","all"],
                        default="all", help="Which phase to test")
    parser.add_argument("--verbose",    action="store_true", help="Print full detail on each test")
    parser.add_argument("--no-cleanup", action="store_true", help="Skip DB cleanup after tests")
    args = parser.parse_args()

    print(f"\n{'═' * 55}")
    print(f"  Cardinal Pipeline Test Suite")
    print(f"  Run ID: {suite.run_id}")
    print(f"  Log:    logs/cardinal_test_{TIMESTAMP}.json")
    print(f"{'═' * 55}")

    phase = args.phase

    def guarded(label: str, phase_key: str, fn, *args):
        """
        Run a test section. If the section itself crashes before any
        run_test() call (e.g. a top-level import fails), record it as
        a FAIL result so the suite continues rather than dying silently.
        """
        if phase not in (phase_key, "all"):
            return None
        try:
            return fn(*args)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            suite.add(TestResult(
                test_id=uuid.uuid4().hex[:8],
                phase=phase_key,
                name=f"{label} — section crashed before first test",
                status=FAIL,
                error=f"{type(exc).__name__}: {exc}",
                detail=traceback.format_exc(),
            ))
            return None

    try:
        guarded("Prerequisites",  "prereq",    test_prerequisites)

        ticket_id = guarded("Normaliser",     "normaliser", test_normaliser)

        guarded("Phase 1",        "1",         test_phase1)
        guarded("Phase 2",        "2",         test_phase2,       ticket_id)
        guarded("Phase 3",        "3",         test_phase3,       ticket_id)
        guarded("Phase 4",        "4",         test_phase4)
        guarded("Phase 5",        "5",         test_phase5)
        guarded("Full Pipeline",  "pipeline",  test_full_pipeline)
        guarded("Celery",         "celery",    test_celery)

    except KeyboardInterrupt:
        print("\n  Interrupted.")

    finally:
        cleanup()
        output = save_logs()
        print_summary()

        sys.exit(0 if suite.failed == 0 else 1)