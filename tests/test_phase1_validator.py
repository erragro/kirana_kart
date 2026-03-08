"""
tests/test_phase1_validator.py
==============================
Unit tests for app/l2_cardinal/phase1_validator.py

All DB calls are patched out — these tests cover only the in-process
validation logic (checks 1-6) without requiring a PostgreSQL connection.
The DB-backed checks (customer block, order existence) are tested with
mocked return values.
"""

import pytest
from unittest.mock import patch, MagicMock

from app.l1_ingestion.schemas import CardinalIngestRequest
from app.l2_cardinal import phase1_validator
from app.l2_cardinal.phase1_validator import Phase1ValidationResult


# ============================================================
# HELPERS
# ============================================================

def _make_request(
    description="Valid description for testing purposes",
    subject="Order not received",
    order_id=None,
    cx_email="user@example.com",
    customer_id=None,
    source="freshdesk",
    org="testorg",
    img_flg=0,
    attachment=0,
) -> CardinalIngestRequest:
    payload = {
        "description": description,
        "subject":     subject,
        "cx_email":    cx_email,
        "img_flg":     img_flg,
        "attachment":  attachment,
    }
    if order_id:
        payload["order_id"] = order_id
    if customer_id:
        payload["customer_id"] = customer_id

    return CardinalIngestRequest(
        org=org,
        channel="email",
        source=source,
        business_line="ecommerce",
        module="delivery",
        payload=payload,
    )


# ============================================================
# DESCRIPTION CHECKS
# ============================================================

class TestDescriptionChecks:

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    @patch("app.l2_cardinal.phase1_validator._check_order_exists", return_value=True)
    def test_valid_description_passes(self, *_):
        result = phase1_validator.run(_make_request())
        assert result.passed

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    def test_empty_description_fails(self, *_):
        result = phase1_validator.run(_make_request(description=""))
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "DESCRIPTION_MISSING" in codes

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    def test_whitespace_only_description_fails(self, *_):
        result = phase1_validator.run(_make_request(description="   \t\n  "))
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "DESCRIPTION_MISSING" in codes

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    def test_too_short_description_fails(self, *_):
        result = phase1_validator.run(_make_request(description="short"))
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "DESCRIPTION_TOO_SHORT" in codes

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    def test_too_long_description_fails(self, *_):
        result = phase1_validator.run(_make_request(description="x" * 50_001))
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "DESCRIPTION_TOO_LONG" in codes


# ============================================================
# SUBJECT CHECKS
# ============================================================

class TestSubjectChecks:

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    @patch("app.l2_cardinal.phase1_validator._check_order_exists", return_value=True)
    def test_empty_subject_allowed(self, *_):
        result = phase1_validator.run(_make_request(subject=""))
        assert result.passed

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    def test_subject_too_long_fails(self, *_):
        result = phase1_validator.run(_make_request(subject="s" * 501))
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "SUBJECT_TOO_LONG" in codes


# ============================================================
# ORDER ID CHECKS
# ============================================================

class TestOrderIdChecks:

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    @patch("app.l2_cardinal.phase1_validator._check_order_exists", return_value=True)
    def test_valid_order_id_passes(self, *_):
        result = phase1_validator.run(_make_request(order_id="ORD-12345"))
        assert result.passed

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    def test_order_id_with_spaces_fails(self, *_):
        result = phase1_validator.run(_make_request(order_id="ORD 12345"))
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "ORDER_ID_INVALID_FORMAT" in codes

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    def test_order_id_too_long_fails(self, *_):
        result = phase1_validator.run(_make_request(order_id="A" * 101))
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "ORDER_ID_TOO_LONG" in codes


# ============================================================
# INJECTION GUARD
# ============================================================

class TestInjectionGuard:

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    def test_sql_injection_in_description_fails(self, *_):
        result = phase1_validator.run(
            _make_request(description="ORDER; DROP TABLE fdraw; --")
        )
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "INJECTION_PATTERN_DETECTED" in codes

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    def test_union_select_in_subject_fails(self, *_):
        result = phase1_validator.run(
            _make_request(subject="UNION SELECT * FROM admin_users")
        )
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "INJECTION_PATTERN_DETECTED" in codes


# ============================================================
# CUSTOMER IDENTIFIER (non-Freshdesk)
# ============================================================

class TestCustomerIdentifier:

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    @patch("app.l2_cardinal.phase1_validator._check_order_exists", return_value=True)
    def test_api_source_without_identifier_fails(self, *_):
        result = phase1_validator.run(
            _make_request(source="api", cx_email=None, customer_id=None)
        )
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "CUSTOMER_IDENTIFIER_MISSING" in codes

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    @patch("app.l2_cardinal.phase1_validator._check_order_exists", return_value=True)
    def test_api_source_with_email_passes(self, *_):
        result = phase1_validator.run(
            _make_request(source="api", cx_email="user@example.com")
        )
        assert result.passed

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    @patch("app.l2_cardinal.phase1_validator._check_order_exists", return_value=True)
    def test_freshdesk_allows_missing_identifier(self, *_):
        # Freshdesk is trusted — no identifier check
        result = phase1_validator.run(
            _make_request(source="freshdesk", cx_email=None, customer_id=None)
        )
        assert result.passed


# ============================================================
# CUSTOMER BLOCK
# ============================================================

class TestCustomerBlock:

    @patch(
        "app.l2_cardinal.phase1_validator._check_customer_blocked",
        return_value=(True, "Fraud risk"),
    )
    def test_blocked_customer_returns_403_code(self, *_):
        result = phase1_validator.run(_make_request(org="realorg"))
        assert not result.passed
        codes = [f.error_code for f in result.failures]
        assert "CUSTOMER_BLOCKED" in codes

    @patch(
        "app.l2_cardinal.phase1_validator._check_customer_blocked",
        return_value=(False, None),
    )
    @patch("app.l2_cardinal.phase1_validator._check_order_exists", return_value=True)
    def test_unblocked_customer_passes(self, *_):
        result = phase1_validator.run(_make_request(org="realorg"))
        assert result.passed


# ============================================================
# SANDBOX MODE
# ============================================================

class TestSandboxMode:

    def test_testorg_is_sandbox(self):
        req = _make_request(org="testorg")
        assert phase1_validator._is_sandbox(req)

    def test_sandbox_prefix_is_sandbox(self):
        req = _make_request(org="sandbox_acme")
        assert phase1_validator._is_sandbox(req)

    def test_production_org_is_not_sandbox(self):
        req = _make_request(org="AcmeCorp")
        assert not phase1_validator._is_sandbox(req)


# ============================================================
# IMG FLAG WARNINGS
# ============================================================

class TestImgFlagWarnings:

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    @patch("app.l2_cardinal.phase1_validator._check_order_exists", return_value=True)
    def test_img_flg_set_but_no_attachment_warns(self, *_):
        result = phase1_validator.run(
            _make_request(img_flg=1, attachment=0)
        )
        assert result.passed         # not a hard failure
        assert len(result.warnings) > 0

    @patch("app.l2_cardinal.phase1_validator._check_customer_blocked", return_value=(False, None))
    @patch("app.l2_cardinal.phase1_validator._check_order_exists", return_value=True)
    def test_attachment_without_img_flg_warns(self, *_):
        result = phase1_validator.run(
            _make_request(img_flg=0, attachment=2)
        )
        assert result.passed
        assert len(result.warnings) > 0
