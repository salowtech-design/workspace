"""
Unit Tests for Security Remediation Capabilities:
1. Offline Two-Person Emergency Break-Glass Access (apps.accounts.auth.emergency_access)
2. Risk-Engine Login Policy Decisions (apps.accounts.auth.risk_engine.decide_login_risk)
3. Multi-Window and Subnet Password Spraying Detection (apps.accounts.auth.spraying_detection)
"""

from datetime import timedelta
import pyotp
import pytest
from django.contrib.auth.hashers import make_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.auth.crypto import encrypt_totp_secret
from apps.accounts.auth.device_approval import create_device_approval_request
from apps.accounts.auth.emergency_access import activate_independent_emergency_access
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.risk_engine import RiskDecision, decide_login_risk
from apps.accounts.auth.spraying_detection import record_auth_failure_and_check_spray
from apps.accounts.models import (
    AuthEvent,
    CustomUser,
    DeviceApprovalRequest,
    IAMAdminIdentity,
    SecurityAlert,
    UserSession,
)
from apps.core.models import RateLimitRecord


@pytest.fixture
def superadmin_pair(db):
    u1 = CustomUser.objects.create(
        employee_id="ST-ADM-0001",
        full_name="Super Admin Alpha",
        email="admin.alpha@salowtech.com",
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
    )
    u1.set_password("AdminAlphaPass2026!@#")
    u1.save()

    u2 = CustomUser.objects.create(
        employee_id="ST-ADM-0002",
        full_name="Super Admin Beta",
        email="admin.beta@salowtech.com",
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
    )
    u2.set_password("AdminBetaPass2026!@#")
    u2.save()

    s1 = pyotp.random_base32()
    enc1, nonce1, kid1 = encrypt_totp_secret(s1)
    admin1 = IAMAdminIdentity.objects.create(
        admin_id="IAM-SUPER-01",
        user=u1,
        password_hash=make_password("AdminAlphaPass2026!@#"),
        encrypted_totp_secret=enc1,
        totp_nonce=nonce1,
        totp_key_id=kid1,
        totp_confirmed_at=timezone.now(),
        role=IAMAdminIdentity.Role.SUPER_ADMIN,
        status=IAMAdminIdentity.Status.ACTIVE,
    )

    s2 = pyotp.random_base32()
    enc2, nonce2, kid2 = encrypt_totp_secret(s2)
    admin2 = IAMAdminIdentity.objects.create(
        admin_id="IAM-SUPER-02",
        user=u2,
        password_hash=make_password("AdminBetaPass2026!@#"),
        encrypted_totp_secret=enc2,
        totp_nonce=nonce2,
        totp_key_id=kid2,
        totp_confirmed_at=timezone.now(),
        role=IAMAdminIdentity.Role.SUPER_ADMIN,
        status=IAMAdminIdentity.Status.ACTIVE,
    )

    return admin1, s1, admin2, s2


@pytest.mark.django_db
class TestIndependentEmergencyAccess:
    def test_two_person_emergency_access_success(self, superadmin_pair):
        """Two distinct Super Admins with valid credentials and fresh TOTP successfully activate break-glass session."""
        admin1, s1, admin2, s2 = superadmin_pair
        code1 = pyotp.TOTP(s1).now()
        code2 = pyotp.TOTP(s2).now()

        bearer, meta = activate_independent_emergency_access(
            custodian_1_id=admin1.admin_id,
            custodian_1_password="AdminAlphaPass2026!@#",
            custodian_1_totp=code1,
            custodian_2_id=admin2.admin_id,
            custodian_2_password="AdminBetaPass2026!@#",
            custodian_2_totp=code2,
            incident_id="INC-2026-CRIT-001",
            scope="cluster_recovery",
            reason="Primary database gateway partition requiring emergency schema recovery.",
            duration_minutes=15,
        )

        assert bearer.startswith("salow-emg-")
        assert meta["incident_id"] == "INC-2026-CRIT-001"
        assert meta["custodian_1"] == admin1.admin_id
        assert meta["custodian_2"] == admin2.admin_id

        # Verify CRITICAL SecurityAlert generated
        alert = SecurityAlert.objects.filter(
            category=SecurityAlert.Category.BREAK_GLASS_ACCESS,
            severity=SecurityAlert.Severity.CRITICAL,
            title__icontains="EMERGENCY ACCESS ACTIVATED",
        ).first()
        assert alert is not None
        assert "INC-2026-CRIT-001" in alert.description

    def test_self_authorization_rejected(self, superadmin_pair):
        """Same custodian cannot authorize both sides of emergency break-glass."""
        admin1, s1, _, _ = superadmin_pair
        code1 = pyotp.TOTP(s1).now()

        with pytest.raises(PermissionDenied) as exc:
            activate_independent_emergency_access(
                custodian_1_id=admin1.admin_id,
                custodian_1_password="AdminAlphaPass2026!@#",
                custodian_1_totp=code1,
                custodian_2_id=admin1.admin_id,
                custodian_2_password="AdminAlphaPass2026!@#",
                custodian_2_totp=code1,
                incident_id="INC-2026-SELF",
                scope="cluster_recovery",
                reason="Testing self authorization rejection.",
            )
        assert "distinct custodians strictly required" in str(exc.value).lower() or "prohibits self-authorization" in str(exc.value).lower()

    def test_short_justification_rejected(self, superadmin_pair):
        """Justification shorter than 20 characters is rejected."""
        admin1, s1, admin2, s2 = superadmin_pair
        code1 = pyotp.TOTP(s1).now()
        code2 = pyotp.TOTP(s2).now()

        with pytest.raises(ValidationError) as exc:
            activate_independent_emergency_access(
                custodian_1_id=admin1.admin_id,
                custodian_1_password="AdminAlphaPass2026!@#",
                custodian_1_totp=code1,
                custodian_2_id=admin2.admin_id,
                custodian_2_password="AdminBetaPass2026!@#",
                custodian_2_totp=code2,
                incident_id="INC-2026-SHORT",
                scope="cluster_recovery",
                reason="Too short",
            )
        assert "minimum 20 characters" in str(exc.value)


@pytest.mark.django_db
class TestRiskEngineLoginPolicy:
    def test_after_hours_alone_never_blocks_employee(self):
        """Baseline login without risk signals is ALLOW (even if after-hours adds +10)."""
        emp = CustomUser.objects.create(
            employee_id="ST-EMP-LATE",
            full_name="Late Worker",
            email="late@salowtech.com",
            role=CustomUser.Role.EMPLOYEE,
            status=CustomUser.Status.ACTIVE,
        )
        emp.set_password("LateNight2026!@#")
        emp.save()

        # Seed existing session with this IP so it's not a new network IP
        UserSession.objects.create(
            user=emp,
            session_key="prior-session-key",
            ip_address="192.168.1.50",
            mfa_verified=True,
        )

        decision, score, signals = decide_login_risk(emp, "192.168.1.50", "LateNightBrowser/1.0")
        assert decision == RiskDecision.ALLOW
        assert score < 40

    def test_risk_threshold_tiers(self):
        """Validates ALLOW, CHALLENGE, DENY, and CONTAIN risk tiers."""
        emp = CustomUser.objects.create(
            employee_id="ST-EMP-TIER",
            full_name="Tier Worker",
            email="tier@salowtech.com",
            role=CustomUser.Role.EMPLOYEE,
            status=CustomUser.Status.ACTIVE,
        )

        # Baseline: score 0 -> ALLOW
        dec, score, _ = decide_login_risk(emp, "10.0.0.1", "StandardBrowser/1.0")
        assert dec == RiskDecision.ALLOW

        # Add prior session at different IP, so 10.0.0.1 becomes new network IP (+25)
        # Plus 2 recent failures (+35) => score 60 -> CHALLENGE (40-74)
        UserSession.objects.create(user=emp, session_key="k1", ip_address="10.0.0.99")
        for _ in range(2):
            AuthEvent.objects.create(
                event="login_failed",
                code="AUTH-LOK-001",
                step="password",
                employee_id=emp.employee_id,
                network_address="10.0.0.1",
            )
        dec, score, _ = decide_login_risk(emp, "10.0.0.1", "StandardBrowser/1.0")
        assert dec == RiskDecision.CHALLENGE
        assert 40 <= score < 75

        # Elevate to Super Admin role (+15), score = 25 + 35 + 15 = 75 -> DENY (75-89)
        emp.role = CustomUser.Role.SUPER_ADMIN
        emp.save(update_fields=["role"])
        dec, score, _ = decide_login_risk(emp, "10.0.0.1", "StandardBrowser/1.0")
        assert dec in (RiskDecision.DENY, RiskDecision.CONTAIN)

        # Add 2 recent device approval requests (+30), score = 75 + 30 = 100 -> CONTAIN (>= 90)
        for _ in range(2):
            create_device_approval_request(user=emp)
        dec, score, _ = decide_login_risk(emp, "10.0.0.1", "StandardBrowser/1.0")
        assert dec == RiskDecision.CONTAIN
        assert score >= 90


@pytest.mark.django_db
class TestMultiWindowSprayingDetection:
    def test_subnet_clustering_and_rate_limiting(self):
        """Attacking >= 8 distinct accounts across a /24 subnet triggers IP throttling."""
        subnet_ips = [f"203.0.113.{i}" for i in range(1, 10)]

        for i, ip in enumerate(subnet_ips, start=1):
            AuthEvent.objects.create(
                event="login_failed",
                code="AUTH-LOK-001",
                step="password",
                employee_id=f"ST-SPRAY-TARGET-{i}",
                network_address=ip,
            )
            detected = record_auth_failure_and_check_spray(ip, f"ST-SPRAY-TARGET-{i}")
            if i < 8:
                assert detected is False
            else:
                assert detected is True

        # Confirm subnet rate-limit record exists and immediately exceeds sensitive threshold (>= 10 records)
        records = RateLimitRecord.objects.filter(key=f"{subnet_ips[-1]}:sensitive")
        assert records.count() >= 10, f"Expected at least 10 records for immediate block, got {records.count()}"

    def test_spraying_detection_blocks_subsequent_request_immediately(self):
        """Spraying detection inserts sufficient records to immediately block attacker IP."""
        test_ip = "198.51.100.42"
        for i in range(1, 6):
            AuthEvent.objects.create(
                event="login_failed",
                code="AUTH-LOK-001",
                step="password",
                employee_id=f"ST-FAST-TARGET-{i}",
                network_address=test_ip,
            )
        detected = record_auth_failure_and_check_spray(test_ip, "ST-FAST-TARGET-5")
        assert detected is True

        # RateLimitRecord count must exceed SENSITIVE_LIMIT (5)
        count = RateLimitRecord.objects.filter(key=f"{test_ip}:sensitive").count()
        assert count >= 10


@pytest.mark.django_db
class TestKeyRotationAndOutboxRobustness:
    def test_email_outbox_encrypt_with_key_id(self):
        """encrypt_payload accepts specific key_id and decrypts round-trip."""
        from apps.core.email_service import decrypt_payload, encrypt_payload

        payload = {"otp": "123456", "template": "Test code: 123456"}
        enc, nonce, kid = encrypt_payload(payload, key_id="k1")
        assert kid == "k1"
        assert len(enc) > 0

        dec = decrypt_payload(enc, nonce, kid)
        assert dec["otp"] == "123456"

    def test_publish_event_idempotent_duplicate(self):
        """publish_event returns existing event when duplicate event_id is supplied."""
        from apps.core.outbox import publish_event

        e1 = publish_event("test.event", {"data": 1}, event_id="unique-evt-001")
        e2 = publish_event("test.event", {"data": 2}, event_id="unique-evt-001")
        assert e1.event_id == e2.event_id == "unique-evt-001"
        assert e2.payload == {"data": 1}
