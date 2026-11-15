from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import CustomUser
from apps.attendance.services import get_current_kolkata_date
from apps.core.models import AuditLog
from apps.core.services import redact_payload
from apps.face.engine.stub import StubVerifier


@pytest.mark.django_db
def test_custom_user_creation_and_normalization():
    """Verify employee ID is normalized and custom user fields persist."""
    user = CustomUser.objects.create_user(
        employee_id=" emp101 ",
        full_name="Alice Developer",
        password="ValidPassword123!",
    )
    assert user.employee_id == "EMP101"
    assert user.role == CustomUser.Role.EMPLOYEE
    assert user.status == CustomUser.Status.ACTIVE
    assert user.check_password("ValidPassword123!") is True


@pytest.mark.django_db
def test_custom_user_employee_id_immutability():
    """Verify that employee_id cannot be altered after creation."""
    user = CustomUser.objects.create_user(
        employee_id="EMP102",
        full_name="Bob Engineer",
        password="ValidPassword123!",
    )
    user.employee_id = "EMP999"
    with pytest.raises(ValidationError, match="Employee ID is immutable"):
        user.save()


@pytest.mark.django_db
def test_audit_log_append_only_enforcement():
    """Verify AuditLog records cannot be updated or deleted."""
    log = AuditLog.objects.create(
        action="test.action",
        target_type="system",
        target_id="1",
        payload_summary={"meta": "info"},
    )
    assert log.id is not None

    with pytest.raises(PermissionDenied, match="immutable"):
        log.save()

    with pytest.raises(PermissionDenied, match="cannot be deleted"):
        log.delete()


def test_audit_log_payload_redaction():
    """Verify that sensitive credential and biometric keys are redacted."""
    sensitive_payload = {
        "user": "EMP101",
        "password": "SuperSecretPassword",
        "token": "abc123token",
        "raw_image": b"binary_image_data",
        "safe_field": "public_data",
    }
    cleaned = redact_payload(sensitive_payload)
    assert cleaned["password"] == "[REDACTED]"
    assert cleaned["token"] == "[REDACTED]"
    assert cleaned["raw_image"] == "[REDACTED]"
    assert cleaned["safe_field"] == "public_data"


def test_kolkata_business_date_derivation():
    """Verify UTC instant to Asia/Kolkata date translation."""
    # 2026-11-15 19:00:00 UTC is 2026-11-16 00:30:00 IST (+5:30)
    utc_instant = datetime(2026, 11, 15, 19, 0, 0, tzinfo=dt_timezone.utc)
    kolkata_date = get_current_kolkata_date(utc_instant)
    assert kolkata_date.day == 16
    assert kolkata_date.month == 11
    assert kolkata_date.year == 2026


def test_stub_verifier_security_gate():
    """Verify StubVerifier fails closed if not explicitly allowed."""
    with pytest.raises(RuntimeError, match="DEV_VERIFIER is disabled"):
        StubVerifier(allowed=False)

    verifier = StubVerifier(allowed=True)
    res = verifier.verify("EMP101", "nonce", [])
    assert res.success is True
    assert res.method == "dev_stub"
