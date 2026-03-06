from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ============================================================
# CONSTANTS
# Derived from rule_registry.module_name and actual data in
# cardinal_execution_plans, fdraw, and knowledge_base_versions.
# Adding a new module requires updating VALID_MODULES only.
# ============================================================

VALID_CHANNELS: set[str] = {"email", "chat", "voice", "api"}

VALID_SOURCES: set[str] = {"freshdesk", "gmail", "api", "webhook"}

VALID_BUSINESS_LINES: set[str] = {"ecommerce", "fmcg", "internal"}

# Matches rule_registry.module_name slugs used in policy routing
VALID_MODULES: set[str] = {
    "delivery",
    "quality",
    "payment",
    "fraud",
    "compliance",
    "food_safety",
    "fmcg",
    "operations",
}

VALID_ENVIRONMENTS: set[str] = {"production", "staging", "sandbox", "development"}


# ============================================================
# SUB-SCHEMAS
# Nested objects within the inbound payload.
# Each sub-schema validates one concern independently.
# ============================================================


class InboundMetadata(BaseModel):
    """
    Caller-supplied metadata for traceability and routing.
    None of these fields affect business logic — they are
    audit and observability fields only.
    """

    environment: Literal["production", "staging", "sandbox", "development"] = "production"
    called_by: Optional[str] = None          # agent | manual | automation | webhook
    agent_id: Optional[str] = None           # AGT-123 if called by an agent
    test_mode: bool = False
    reprocess: bool = False                  # True if this is a retry of a known ticket
    reprocess_reason: Optional[str] = None   # Why reprocessing: customer_reply | correction
    source_webhook_id: Optional[str] = None  # Freshdesk webhook delivery ID for dedup audit

    model_config = {"extra": "allow"}        # Forward-compatible: unknown metadata keys ignored


class FreshDeskPayload(BaseModel):
    """
    Payload shape expected from Freshdesk webhook.
    Maps directly to fdraw columns.
    """

    ticket_id: int = Field(..., gt=0)
    group_id: str
    group_name: Optional[str] = None
    cx_email: Optional[str] = None  # str not EmailStr — format validation is Phase 1's job
    subject: Optional[str] = None
    description: Optional[str] = None
    status: Optional[int] = None
    tags: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    thread_id: Optional[str] = None
    attachment: Optional[int] = 0
    img_flg: Optional[int] = 0

    # Order context — may be present in Freshdesk custom fields
    order_id: Optional[str] = None
    customer_id: Optional[str] = None

    model_config = {"extra": "allow"}        # Freshdesk sends many fields; allow but ignore extras


class DirectAPIPayload(BaseModel):
    """
    Payload shape for direct API calls (not via Freshdesk).
    Caller must supply minimum viable fields for pipeline processing.
    """

    cx_email: Optional[str] = None           # str not EmailStr — format validation is Phase 1's job
    customer_id: Optional[str] = None        # At least one of cx_email or customer_id required
    subject: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1)
    order_id: Optional[str] = None
    thread_id: Optional[str] = None
    attachment_urls: Optional[List[str]] = None

    @model_validator(mode="after")
    def require_customer_identifier(self) -> DirectAPIPayload:
        if not self.cx_email and not self.customer_id:
            raise ValueError(
                "At least one of cx_email or customer_id must be provided."
            )
        return self

    model_config = {"extra": "allow"}


# ============================================================
# PRIMARY INBOUND SCHEMA
# This is what every caller sends to POST /cardinal/ingest.
# Replaces the original CardinalRequest in the project root.
# ============================================================


class CardinalIngestRequest(BaseModel):
    """
    Single entry point schema for all Cardinal ingestion.

    channel      — How the ticket arrived. Drives Phase 3 source handler.
    source       — Which system sent it. Used for dedup audit logging.
    org          — Organisation identifier. 'Sandbox' = test mode.
    business_line — Top-level product domain. Drives KB selection.
    module       — Sub-domain within business_line. Maps to rule_registry.module_name.
    payload      — Raw ticket data. Shape varies by source (see sub-schemas).
    metadata     — Traceability fields. No business logic impact.
    """

    channel: str = Field(..., description="email | chat | voice | api")
    source: str = Field(..., description="freshdesk | gmail | api | webhook")
    org: str = Field(..., min_length=1, max_length=100)
    business_line: str = Field(..., description="ecommerce | fmcg | internal")
    module: str = Field(
        ...,
        description=(
            "Sub-domain module. Maps to rule_registry.module_name. "
            "delivery | quality | payment | fraud | compliance | "
            "food_safety | fmcg | operations"
        ),
    )
    payload: Dict[str, Any] = Field(..., description="Raw ticket data from source system")
    metadata: Optional[InboundMetadata] = None

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, v: str) -> str:
        val = v.lower().strip()
        if val not in VALID_CHANNELS:
            raise ValueError(
                f"Invalid channel '{v}'. Must be one of: {sorted(VALID_CHANNELS)}"
            )
        return val

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        val = v.lower().strip()
        if val not in VALID_SOURCES:
            raise ValueError(
                f"Invalid source '{v}'. Must be one of: {sorted(VALID_SOURCES)}"
            )
        return val

    @field_validator("business_line")
    @classmethod
    def validate_business_line(cls, v: str) -> str:
        val = v.lower().strip()
        if val not in VALID_BUSINESS_LINES:
            raise ValueError(
                f"Invalid business_line '{v}'. Must be one of: {sorted(VALID_BUSINESS_LINES)}"
            )
        return val

    @field_validator("module")
    @classmethod
    def validate_module(cls, v: str) -> str:
        val = v.lower().strip()
        if val not in VALID_MODULES:
            raise ValueError(
                f"Invalid module '{v}'. Must be one of: {sorted(VALID_MODULES)}"
            )
        return val

    @field_validator("org")
    @classmethod
    def normalise_org(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def sandbox_implies_test_mode(self) -> CardinalIngestRequest:
        """
        If org is Sandbox/TestOrg, force metadata.test_mode = True
        regardless of what the caller sent. Prevents sandbox traffic
        from accidentally being treated as production.
        """
        if self.org.lower() in ("sandbox", "testorg") or self.org.lower().startswith(
            ("sandbox_", "test_", "dev_", "staging_")
        ):
            if self.metadata is None:
                self.metadata = InboundMetadata(environment="sandbox", test_mode=True)
            else:
                self.metadata.test_mode = True
                if self.metadata.environment == "production":
                    self.metadata.environment = "sandbox"
        return self

    model_config = {"extra": "forbid"}      # Unknown top-level keys are rejected hard


# ============================================================
# CANONICAL PAYLOAD SCHEMA
# Output of Phase 1 normaliser. This is the standardised
# representation written to fdraw.canonical_payload and
# passed to every downstream phase.
# ============================================================


class CanonicalPayload(BaseModel):
    """
    Normalised representation of any inbound ticket.
    Produced by normaliser.py from CardinalIngestRequest.
    Written to fdraw.canonical_payload (jsonb).
    Consumed by Phase 2 (hash), Phase 4 (enrich), Phase 5 (dispatch).

    Field names match fdraw columns wherever possible to
    avoid translation overhead at DB write time.
    """

    # Core identity
    org: str
    channel: str
    source: str
    business_line: str
    module: str
    is_sandbox: bool

    # Ticket fields — normalised from any source
    ticket_id: Optional[int] = None          # Populated if source provided one (Freshdesk)
    thread_id: Optional[str] = None
    cx_email: Optional[str] = None
    customer_id: Optional[str] = None
    order_id: Optional[str] = None
    subject: Optional[str] = None
    description: Optional[str] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    tags: Optional[str] = None
    attachment: int = 0
    img_flg: int = 0
    source_created_at: Optional[datetime] = None
    source_updated_at: Optional[datetime] = None

    # Enrichment fields — populated later in pipeline
    # Included here so the schema is the single source of truth
    # for what canonical_payload can contain at any stage.
    detected_language: Optional[str] = None
    preprocessing_version: Optional[str] = None
    payload_hash: Optional[str] = None       # Set by Phase 2 after dedup check

    # Metadata passthrough
    environment: str = "production"
    called_by: Optional[str] = None
    agent_id: Optional[str] = None
    reprocess: bool = False
    reprocess_reason: Optional[str] = None

    # Raw original payload preserved for audit / debugging
    raw_payload: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}        # Downstream phases may add fields


# ============================================================
# RESPONSE SCHEMAS
# Standardised API response shapes returned by router.py.
# ============================================================


class IngestResponse(BaseModel):
    """
    202 Accepted response from POST /cardinal/ingest.
    Returned immediately — processing is async.
    """

    execution_id: str
    ticket_id: Optional[int] = None          # None if ticket not yet written to fdraw
    status: Literal["accepted", "duplicate", "rejected"] = "accepted"
    message: str
    is_sandbox: bool
    received_at: datetime


class DuplicateResponse(BaseModel):
    """
    200 response when payload is detected as duplicate by Phase 2.
    Not a 4xx — the caller's request was valid, it was just a duplicate.
    """

    status: Literal["duplicate"] = "duplicate"
    original_ticket_id: Optional[int] = None
    payload_hash: str
    message: str = "Duplicate payload detected within 24-hour deduplication window."


class ErrorResponse(BaseModel):
    """
    Standard error response for validation failures and system errors.
    """

    status: Literal["error"] = "error"
    error_code: str                          # VALIDATION_ERROR | SYSTEM_ERROR | UNSUPPORTED_CHANNEL
    message: str
    detail: Optional[Any] = None             # Pydantic validation error details if applicable