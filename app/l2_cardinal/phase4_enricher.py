"""
app/l2_cardinal/phase4_enricher.py
====================================
Cardinal Phase 4 — Customer & Order Enrichment

Responsibility:
    Assemble the complete CustomerContext that the LLM pipeline
    (Celery workers, L4 agents) needs to evaluate a ticket.

    Everything that requires a DB lookup before the LLM runs
    happens here — in one phase, in one place.

This module does NOT:
    - Make LLM calls
    - Apply business rules
    - Make routing decisions (Phase 5)
    - Write to any table except fdraw (pipeline_stage update only)

Position in pipeline:
    Phase 3 (source handler) → Phase 4 (here) → Phase 5 (dispatcher)

What is assembled:

    1. Customer identity resolution
       - Resolve customer_id from cx_email if only email was provided
       - If customer not found: create a minimal unknown-customer context
         rather than failing — the LLM pipeline handles new customers

    2. Customer profile
       - All columns from customers table (base identity + 004 migration columns)
       - Determines membership_tier, vip_flag, dietary_preference, block status

    3. Customer risk profile
       - All columns from customer_risk_profile (hourly-computed signals)
       - fraud_score, auto_approval_limit, recommended_queue
       - If risk profile missing (new customer, or compute job hasn't run yet):
         use safe defaults (fraud_score=0, STANDARD_REVIEW queue, limit=500)

    4. Order context
       - order_id, order_value, sla_breach, delivery_estimated, delivery_actual
       - Computed fields: delivery_delay_minutes, is_high_value
       - If order not found: None order context — LLM handles gracefully

    5. Active policy version
       - Reads kb_runtime_config for active_version + shadow_version
       - Reads policy_versions to confirm it is_active and vectorised
       - If no active policy: raises EnrichmentError — cannot process
         without a policy. This is a hard fail.

    6. Prior complaint count
       - Count of complaints by this customer in last 30 days
       - Used by Phase 5 for escalation_group assignment
       - Separate from customer_risk_profile.complaints_last_30_days
         because that is hourly-computed; this is the live count

Output: Phase4Result containing CustomerContext + active policy version

CustomerContext is serialised to JSON and written into
fdraw.canonical_payload so the Celery worker has full context
without needing to re-query the DB.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from app.l1_ingestion.schemas import CanonicalPayload
from app.l2_cardinal.phase3_handler import Phase3Result

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

# High-value order threshold — matches FRAUD-002 definition
HIGH_VALUE_ORDER_THRESHOLD = 1000.0

logger = logging.getLogger("cardinal.phase4")


# ============================================================
# CONTEXT DATACLASSES
# These are the output types. They are serialisable to dict/JSON
# so they can be embedded in fdraw.canonical_payload (jsonb).
# ============================================================

@dataclass
class CustomerProfile:
    """
    Combined row from customers + migration 004 columns.
    Defaults represent a new/unknown customer.
    """
    customer_id:          Optional[str]   = None
    email:                Optional[str]   = None
    phone:                Optional[str]   = None
    is_active:            bool            = True
    is_blocked:           bool            = False
    block_reason:         Optional[str]   = None
    segment:              str             = "unknown"
    membership_tier:      str             = "STANDARD"
    lifetime_order_count: int             = 0
    lifetime_igcc_rate:   float           = 0.0
    lifetime_value:       float           = 0.0
    total_refunds:        int             = 0
    total_refund_amount:  float           = 0.0
    dietary_preference:   Optional[str]   = None
    vip_flag:             bool            = False
    abuse_incident_count: int             = 0
    chargebacks_count:    int             = 0
    churn_probability:    Optional[float] = None
    signup_date:          Optional[str]   = None   # ISO string for JSON serialisation
    is_new_customer:      bool            = False   # True if not found in DB


@dataclass
class RiskProfile:
    """
    Row from customer_risk_profile.
    Defaults are the safe/permissive values for unknown customers.
    """
    fraud_score:                 float  = 0.0
    fraud_risk_classification:   str    = "NORMAL"
    fraud_action_recommended:    Optional[str] = None
    orders_last_7_days:          int    = 0
    orders_last_30_days:         int    = 0
    orders_last_90_days:         int    = 0
    refunds_last_7_days:         int    = 0
    refunds_last_30_days:        int    = 0
    refunds_last_90_days:        int    = 0
    refund_rate_7d:              float  = 0.0
    refund_rate_30d:             float  = 0.0
    refund_rate_90d:             float  = 0.0
    complaints_last_30_days:     int    = 0
    marked_delivered_claims_90d: int    = 0
    high_value_orders_30d:       int    = 0
    refunds_on_high_value_30d:   int    = 0
    auto_approval_eligible:      bool   = True
    auto_approval_limit:         float  = 500.0
    auto_approval_blocked_reason: Optional[str] = None
    recommended_queue:           str    = "STANDARD_REVIEW"
    last_computed_at:            Optional[str] = None   # ISO string
    is_default:                  bool   = False  # True if no risk profile found in DB


@dataclass
class OrderContext:
    """
    Order data for this ticket's associated order.
    None if no order_id provided or order not found.
    """
    order_id:               str
    customer_id:            str
    order_value:            float
    delivery_estimated:     Optional[str]  = None  # ISO string
    delivery_actual:        Optional[str]  = None  # ISO string
    sla_breach:             bool           = False
    delivery_delay_minutes: Optional[int]  = None  # Computed: actual - estimated
    is_high_value:          bool           = False  # order_value >= HIGH_VALUE_ORDER_THRESHOLD
    order_found:            bool           = True


@dataclass
class PolicyContext:
    """
    Active policy version resolved from kb_runtime_config + policy_versions.
    """
    active_version:   str
    shadow_version:   Optional[str]
    artifact_hash:    Optional[str]
    vector_collection: Optional[str]
    activated_at:     Optional[str]  # ISO string


@dataclass
class CustomerContext:
    """
    The complete enrichment package passed to the LLM pipeline.
    Embedded in fdraw.canonical_payload as JSON.
    Also passed directly to Phase 5 for routing decisions.
    """
    customer:              CustomerProfile
    risk:                  RiskProfile
    order:                 Optional[OrderContext]
    policy:                PolicyContext
    prior_complaints_30d:  int             = 0   # Live count, not from risk profile
    enriched_at:           str             = ""  # ISO timestamp
    enrichment_version:    str             = "1.0"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict for JSON embedding."""
        return asdict(self)


@dataclass
class Phase4Result:
    """Output of phase4_enricher.run()."""
    canonical:        CanonicalPayload
    context:          CustomerContext
    active_policy:    str              # Shortcut: context.policy.active_version
    customer_id:      Optional[str]    # Resolved customer_id (may differ from canonical)
    warnings:         list[str]        = field(default_factory=list)


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

def run(phase3: Phase3Result) -> Phase4Result:
    """
    Execute Phase 4: Customer & Order Enrichment.

    Steps:
      1. Resolve customer_id (from canonical or via email lookup).
      2. Fetch CustomerProfile from customers table.
      3. Fetch RiskProfile from customer_risk_profile.
      4. Fetch OrderContext from orders table.
      5. Resolve active PolicyContext from kb_runtime_config.
      6. Count prior complaints (live, not from risk profile).
      7. Assemble CustomerContext.
      8. Embed CustomerContext into fdraw.canonical_payload.
      9. Update fdraw.pipeline_stage → ENRICHED.

    Args:
        phase3:  Phase3Result from Phase 3.

    Returns:
        Phase4Result with complete CustomerContext.

    Raises:
        EnrichmentError: If active policy cannot be resolved.
                         All other failures produce warnings and safe defaults.
    """
    canonical = phase3.canonical
    warnings  = list(phase3.warnings)  # carry forward Phase 3 warnings

    # ----------------------------------------------------------
    # Step 1: Resolve customer_id
    # ----------------------------------------------------------
    customer_id = _resolve_customer_id(canonical)
    if not customer_id:
        warnings.append(
            f"Could not resolve customer_id from cx_email='{canonical.cx_email}' "
            f"or customer_id='{canonical.customer_id}'. "
            "Using new-customer defaults."
        )

    # ----------------------------------------------------------
    # Step 2: Fetch customer profile
    # ----------------------------------------------------------
    customer_profile = _fetch_customer_profile(customer_id, canonical.cx_email)

    # ----------------------------------------------------------
    # Step 3: Fetch risk profile
    # ----------------------------------------------------------
    risk_profile = _fetch_risk_profile(customer_id)
    if risk_profile.is_default:
        warnings.append(
            f"No risk profile found for customer_id='{customer_id}'. "
            "Using safe defaults. Risk profile compute job may not have run yet."
        )

    # ----------------------------------------------------------
    # Step 4: Fetch order context
    # ----------------------------------------------------------
    order_context = None
    if canonical.order_id:
        order_context = _fetch_order_context(canonical.order_id)
        if not order_context.order_found:
            warnings.append(
                f"Order '{canonical.order_id}' not found in orders table. "
                "LLM pipeline will proceed without order context."
            )

    # ----------------------------------------------------------
    # Step 5: Resolve active policy
    # ----------------------------------------------------------
    policy_context = _resolve_active_policy()
    # _resolve_active_policy raises EnrichmentError if no active policy found

    # ----------------------------------------------------------
    # Step 6: Count prior complaints (live)
    # ----------------------------------------------------------
    prior_complaints = _count_prior_complaints(customer_id)

    # ----------------------------------------------------------
    # Step 7: Assemble CustomerContext
    # ----------------------------------------------------------
    context = CustomerContext(
        customer=customer_profile,
        risk=risk_profile,
        order=order_context,
        policy=policy_context,
        prior_complaints_30d=prior_complaints,
        enriched_at=datetime.now(timezone.utc).isoformat(),
        enrichment_version="1.0",
    )

    # ----------------------------------------------------------
    # Step 8 & 9: Embed into fdraw.canonical_payload + update stage
    # ----------------------------------------------------------
    _update_fdraw(canonical.ticket_id, context)

    # Update canonical with resolved customer_id so Phase 5 has it
    canonical.customer_id = customer_id or canonical.customer_id

    logger.info(
        "Phase 4 complete | ticket_id=%s | customer_id=%s | "
        "fraud_score=%.3f | policy=%s | prior_complaints=%d | warnings=%d",
        canonical.ticket_id,
        customer_id,
        risk_profile.fraud_score,
        policy_context.active_version,
        prior_complaints,
        len(warnings),
    )

    return Phase4Result(
        canonical=canonical,
        context=context,
        active_policy=policy_context.active_version,
        customer_id=customer_id,
        warnings=warnings,
    )


# ============================================================
# CUSTOMER ID RESOLUTION
# ============================================================

def _resolve_customer_id(canonical: CanonicalPayload) -> Optional[str]:
    """
    Resolve a single customer_id from available identifiers.

    Priority:
      1. canonical.customer_id — already a customer_id, use directly
      2. canonical.cx_email    — look up customers.email → return customer_id
      3. Neither               — return None (new customer path)
    """
    if canonical.customer_id:
        return canonical.customer_id

    if canonical.cx_email:
        conn = _get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT customer_id FROM {SCHEMA}.customers "
                    f"WHERE email = %s LIMIT 1",
                    (canonical.cx_email,),
                )
                row = cur.fetchone()
            return row[0] if row else None
        except psycopg2.Error as exc:
            logger.warning("customer_id resolution failed: %s", exc)
            return None
        finally:
            conn.close()

    return None


# ============================================================
# CUSTOMER PROFILE FETCH
# ============================================================

def _fetch_customer_profile(
    customer_id: Optional[str],
    cx_email:    Optional[str],
) -> CustomerProfile:
    """
    Fetch all columns from customers for this customer.
    Returns CustomerProfile with is_new_customer=True if not found.
    """
    if not customer_id and not cx_email:
        return CustomerProfile(is_new_customer=True)

    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if customer_id:
                cur.execute(
                    f"""
                    SELECT
                        customer_id, email, phone, is_active,
                        is_blocked, block_reason, segment,
                        membership_tier, lifetime_order_count,
                        lifetime_igcc_rate, lifetime_value,
                        total_refunds, total_refund_amount,
                        dietary_preference, vip_flag,
                        abuse_incident_count, chargebacks_count,
                        customer_churn_probability, signup_date
                    FROM {SCHEMA}.customers
                    WHERE customer_id = %s
                    LIMIT 1
                    """,
                    (customer_id,),
                )
            else:
                cur.execute(
                    f"""
                    SELECT
                        customer_id, email, phone, is_active,
                        is_blocked, block_reason, segment,
                        membership_tier, lifetime_order_count,
                        lifetime_igcc_rate, lifetime_value,
                        total_refunds, total_refund_amount,
                        dietary_preference, vip_flag,
                        abuse_incident_count, chargebacks_count,
                        customer_churn_probability, signup_date
                    FROM {SCHEMA}.customers
                    WHERE email = %s
                    LIMIT 1
                    """,
                    (cx_email,),
                )
            row = cur.fetchone()

        if not row:
            return CustomerProfile(
                customer_id=customer_id,
                email=cx_email,
                is_new_customer=True,
            )

        return CustomerProfile(
            customer_id=row["customer_id"],
            email=row["email"],
            phone=row["phone"],
            is_active=row["is_active"],
            is_blocked=bool(row.get("is_blocked", False)),
            block_reason=row.get("block_reason"),
            segment=row["segment"],
            membership_tier=row.get("membership_tier", "STANDARD"),
            lifetime_order_count=row["lifetime_order_count"],
            lifetime_igcc_rate=float(row["lifetime_igcc_rate"] or 0),
            lifetime_value=float(row.get("lifetime_value") or 0),
            total_refunds=int(row.get("total_refunds") or 0),
            total_refund_amount=float(row.get("total_refund_amount") or 0),
            dietary_preference=row.get("dietary_preference"),
            vip_flag=bool(row.get("vip_flag", False)),
            abuse_incident_count=int(row.get("abuse_incident_count") or 0),
            chargebacks_count=int(row.get("chargebacks_count") or 0),
            churn_probability=float(row["customer_churn_probability"])
                if row.get("customer_churn_probability") is not None else None,
            signup_date=row["signup_date"].isoformat()
                if row.get("signup_date") else None,
            is_new_customer=False,
        )

    except psycopg2.Error as exc:
        logger.warning("Customer profile fetch failed: %s", exc)
        return CustomerProfile(customer_id=customer_id, is_new_customer=True)
    finally:
        conn.close()


# ============================================================
# RISK PROFILE FETCH
# ============================================================

def _fetch_risk_profile(customer_id: Optional[str]) -> RiskProfile:
    """
    Fetch customer_risk_profile row.
    Returns RiskProfile with is_default=True if not found.
    """
    if not customer_id:
        return RiskProfile(is_default=True)

    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    fraud_score, fraud_risk_classification,
                    fraud_action_recommended,
                    orders_last_7_days, orders_last_30_days, orders_last_90_days,
                    refunds_last_7_days, refunds_last_30_days, refunds_last_90_days,
                    refund_rate_7d, refund_rate_30d, refund_rate_90d,
                    complaints_last_30_days, marked_delivered_claims_90d,
                    high_value_orders_30d, refunds_on_high_value_30d,
                    auto_approval_eligible, auto_approval_limit,
                    auto_approval_blocked_reason, recommended_queue,
                    last_computed_at
                FROM {SCHEMA}.customer_risk_profile
                WHERE customer_id = %s
                LIMIT 1
                """,
                (customer_id,),
            )
            row = cur.fetchone()

        if not row:
            return RiskProfile(is_default=True)

        return RiskProfile(
            fraud_score=float(row["fraud_score"]),
            fraud_risk_classification=row["fraud_risk_classification"],
            fraud_action_recommended=row.get("fraud_action_recommended"),
            orders_last_7_days=row["orders_last_7_days"],
            orders_last_30_days=row["orders_last_30_days"],
            orders_last_90_days=row["orders_last_90_days"],
            refunds_last_7_days=row["refunds_last_7_days"],
            refunds_last_30_days=row["refunds_last_30_days"],
            refunds_last_90_days=row["refunds_last_90_days"],
            refund_rate_7d=float(row["refund_rate_7d"]),
            refund_rate_30d=float(row["refund_rate_30d"]),
            refund_rate_90d=float(row["refund_rate_90d"]),
            complaints_last_30_days=row["complaints_last_30_days"],
            marked_delivered_claims_90d=row["marked_delivered_claims_90d"],
            high_value_orders_30d=row["high_value_orders_30d"],
            refunds_on_high_value_30d=row["refunds_on_high_value_30d"],
            auto_approval_eligible=bool(row["auto_approval_eligible"]),
            auto_approval_limit=float(row["auto_approval_limit"]),
            auto_approval_blocked_reason=row.get("auto_approval_blocked_reason"),
            recommended_queue=row["recommended_queue"],
            last_computed_at=row["last_computed_at"].isoformat()
                if row.get("last_computed_at") else None,
            is_default=False,
        )

    except psycopg2.Error as exc:
        logger.warning("Risk profile fetch failed: %s", exc)
        return RiskProfile(is_default=True)
    finally:
        conn.close()


# ============================================================
# ORDER CONTEXT FETCH
# ============================================================

def _fetch_order_context(order_id: str) -> OrderContext:
    """
    Fetch order row + compute delivery delay.
    Returns OrderContext with order_found=False if not in DB.
    """
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    order_id, customer_id, order_value,
                    delivery_estimated, delivery_actual,
                    sla_breach
                FROM {SCHEMA}.orders
                WHERE order_id = %s
                LIMIT 1
                """,
                (order_id,),
            )
            row = cur.fetchone()

        if not row:
            return OrderContext(
                order_id=order_id,
                customer_id="",
                order_value=0.0,
                order_found=False,
            )

        # Compute delivery delay
        delay_minutes = None
        if row.get("delivery_estimated") and row.get("delivery_actual"):
            delta = row["delivery_actual"] - row["delivery_estimated"]
            delay_minutes = int(delta.total_seconds() / 60)

        return OrderContext(
            order_id=row["order_id"],
            customer_id=row["customer_id"],
            order_value=float(row["order_value"]),
            delivery_estimated=row["delivery_estimated"].isoformat()
                if row.get("delivery_estimated") else None,
            delivery_actual=row["delivery_actual"].isoformat()
                if row.get("delivery_actual") else None,
            sla_breach=bool(row["sla_breach"]),
            delivery_delay_minutes=delay_minutes,
            is_high_value=float(row["order_value"]) >= HIGH_VALUE_ORDER_THRESHOLD,
            order_found=True,
        )

    except psycopg2.Error as exc:
        logger.warning("Order context fetch failed for order_id='%s': %s", order_id, exc)
        return OrderContext(order_id=order_id, customer_id="", order_value=0.0, order_found=False)
    finally:
        conn.close()


# ============================================================
# ACTIVE POLICY RESOLUTION
# ============================================================

def _resolve_active_policy() -> PolicyContext:
    """
    Resolve the active policy version from kb_runtime_config + policy_versions.

    kb_runtime_config holds the single active_version pointer.
    policy_versions confirms it is is_active=True and vector_status='ready'.

    Raises EnrichmentError if:
      - No row in kb_runtime_config
      - Active version not found in policy_versions
      - Active version is not is_active=True

    A policy resolution failure is a hard fail — the LLM pipeline
    cannot run without knowing which rules to apply.
    """
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # Step 1: Get active_version pointer
            cur.execute(
                f"""
                SELECT active_version, shadow_version
                FROM {SCHEMA}.kb_runtime_config
                ORDER BY id DESC
                LIMIT 1
                """
            )
            config_row = cur.fetchone()

        if not config_row:
            raise EnrichmentError(
                "kb_runtime_config is empty. Cannot resolve active policy version. "
                "Run the KB publish workflow to set an active version."
            )

        active_version = config_row["active_version"]
        shadow_version = config_row.get("shadow_version")

        # Step 2: Confirm policy_versions entry exists and is active
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    policy_version, is_active, artifact_hash,
                    vector_collection, activated_at, vector_status
                FROM {SCHEMA}.policy_versions
                WHERE policy_version = %s
                LIMIT 1
                """,
                (active_version,),
            )
            policy_row = cur.fetchone()

        if not policy_row:
            raise EnrichmentError(
                f"Active policy version '{active_version}' listed in kb_runtime_config "
                f"but not found in policy_versions. Re-run KB publish workflow."
            )

        if not policy_row["is_active"]:
            raise EnrichmentError(
                f"Policy version '{active_version}' exists but is_active=False. "
                f"It may have been deactivated. Check kb_runtime_config and policy_versions."
            )

        return PolicyContext(
            active_version=policy_row["policy_version"],
            shadow_version=shadow_version,
            artifact_hash=policy_row.get("artifact_hash"),
            vector_collection=policy_row.get("vector_collection"),
            activated_at=policy_row["activated_at"].isoformat()
                if policy_row.get("activated_at") else None,
        )

    except EnrichmentError:
        raise
    except psycopg2.Error as exc:
        raise EnrichmentError(
            f"DB error resolving active policy: {exc}"
        ) from exc
    finally:
        conn.close()


# ============================================================
# PRIOR COMPLAINTS COUNT (LIVE)
# ============================================================

def _count_prior_complaints(customer_id: Optional[str]) -> int:
    """
    Count complaints raised by this customer in the last 30 days.
    Live query — not from customer_risk_profile which is hourly-computed.

    Returns 0 if customer_id is None or on DB error.
    """
    if not customer_id:
        return 0

    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM {SCHEMA}.complaints
                WHERE customer_id = %s
                AND   raised_at   >= now() - INTERVAL '30 days'
                """,
                (customer_id,),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0
    except psycopg2.Error as exc:
        logger.warning("Prior complaints count failed: %s", exc)
        return 0
    finally:
        conn.close()


# ============================================================
# FDRAW UPDATE
# ============================================================

def _update_fdraw(ticket_id: int, context: CustomerContext) -> None:
    """
    Embed CustomerContext into fdraw.canonical_payload and
    update pipeline_stage to ENRICHED.

    Uses jsonb_set to merge the enrichment into the existing
    canonical_payload rather than overwriting it — preserves
    payload_hash and thread context written by earlier phases.
    """
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.fdraw
                    SET
                        canonical_payload = canonical_payload || %s::jsonb,
                        pipeline_stage    = 'ENRICHED'
                    WHERE ticket_id = %s
                    """,
                    (
                        psycopg2.extras.Json({"customer_context": context.to_dict()}),
                        ticket_id,
                    ),
                )
    except psycopg2.Error as exc:
        # Log and continue — enrichment data is in memory and passed
        # directly to Phase 5. fdraw update failure is not fatal here.
        logger.warning(
            "fdraw enrichment update failed for ticket_id=%s: %s",
            ticket_id, exc,
        )
    finally:
        conn.close()


# ============================================================
# EXCEPTIONS
# ============================================================

class EnrichmentError(Exception):
    """
    Raised on hard failures in Phase 4 (e.g. no active policy).
    pipeline.py catches this and returns 503 to the caller.
    """
    pass