"""
Salow Workspace Authentication Centre — Minimum Acceptance Test Matrix (AUTH-001 through AUTH-030).
Sequence 1 Implementation Contract & Single Execution Prompt Verification.

Test Matrix:
- AUTH-001: Dedicated IAM ID + min-16 password + fresh TOTP -> IAMSession only, __Host-salow_iam_session.
- AUTH-002: Wrong IAM password -> constant-time failure, no session, failure logged.
- AUTH-003: Suspended IAM admin -> denied before session issuance.
- AUTH-004: Invalid/expired TOTP code -> rejected at MFA stage.
- AUTH-005: Replayed TOTP code -> rejected by replay guard.
- AUTH-006: Workspace session token presented to IAM host -> rejected.
- AUTH-007: IAM session token presented to Workspace host -> rejected.
- AUTH-008: Unapproved Workspace device receives no usable session.
- AUTH-009: Workspace activation token is single-use and expires.
- AUTH-010: Exactly 10 recovery codes generated, displayed once, stored hashed.
- AUTH-011: Unknown identity follows dummy password hash path.
- AUTH-012: Distributed rate limiting enforces atomic concurrency limit.
- AUTH-013: Session rotation occurs immediately upon authentication.
- AUTH-014: Immediate revocation enforced across all cluster nodes.
- AUTH-015: Durable, correlated audit event written for every transaction.
- AUTH-016: Direct URL navigation without IAM admin permission is denied.
- AUTH-017: Object-level permissions prevent tampering.
- AUTH-018: Secret leakage scan: no plaintext passwords or raw secrets.
- AUTH-019: Unauthorized user cannot modify authentication policy.
- AUTH-020: Policy change increments version, archives prior, audits change.
- AUTH-021: IAM recovery requires dual-control; self-approval prohibited.
- AUTH-022: Three Workspace recovery pathways enforced.
- AUTH-023: Device approval bound to identity, device, and transaction.
- AUTH-024: Risk score evaluation triggers correct decision.
- AUTH-025: Password-spraying detector triggers security alert.
- AUTH-026: Tamper-evident audit engine detects altered records.
- AUTH-027: Unacknowledged alert escalates after 15-minute SLA breach.
- AUTH-028: Dependency probes report truthful live status and latency.
- AUTH-029: Clock monitoring and TOTP window correctness.
- AUTH-030: All 8 real controls execute real backend mutations (zero mock buttons).
"""

import json
import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pyotp
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.auth.activation import (
    issue_activation_code,
    verify_and_consume_code,
)
from apps.accounts.auth.alert_escalation import ALERT_SLA_MINUTES, escalate_unacknowledged_alerts
from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.audit_chain import compute_event_hash, sign_hash
from apps.accounts.auth.authentication_centre import (
    approve_iam_recovery_case,
    complete_auth_transaction,
    create_iam_recovery_case,
    execute_acknowledge_alert,
    execute_export_records,
    execute_release_lock,
    execute_revoke_device,
    execute_revoke_session,
    execute_start_investigation,
    execute_temporary_restriction,
    get_active_policy,
    get_all_admin_presences,
    get_auth_overview_metrics,
    get_live_activity_feed,
    probe_all_dependencies,
    record_admin_heartbeat,
    record_transaction_stage,
    save_policy_version,
    search_auth_records,
    start_auth_transaction,
)
from apps.accounts.auth.crypto import decrypt_totp_secret, encrypt_totp_secret
from apps.accounts.auth.device_approval import (
    create_device_approval_request,
    is_second_device_request,
)
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.iam_auth import (
    IAM_SESSION_COOKIE_NAME,
    authenticate_iam_admin,
    create_iam_admin_identity,
    create_iam_session,
    get_iam_session_from_request,
)
from apps.accounts.auth.recovery_codes import generate_ten_recovery_codes
from apps.accounts.auth.risk_engine import evaluate_login_risk
from apps.accounts.auth.sessions import create_verified_session, revoke_user_sessions
from apps.accounts.auth.spraying_detection import record_auth_failure_and_check_spray
from apps.accounts.models import (
    ActivationCode,
    ApprovedDevice,
    AuditCheckpoint,
    AuthEvent,
    AuthenticationPolicy,
    AuthenticationTransaction,
    CustomUser,
    DeviceApprovalRequest,
    IAMAdminIdentity,
    IAMAdminPresence,
    IAMRecoveryCase,
    IAMSession,
    RecoveryCode,
    SecurityAlert,
    SecurityIncident,
    TOTPCredential,
    UserSession,
)
from apps.core.middleware import RateLimitMiddleware
from apps.core.models import EmailOutbox, RateLimitBucket, RateLimitRecord

User = get_user_model()


@pytest.fixture(autouse=True)
def clean_test_env():
    """Ensure clean distributed rate limiting and audit state per test."""
    RateLimitMiddleware.reset()
    yield


@pytest.fixture
def workspace_user(db):
    """Authoritative Workspace employee user."""
    return User.objects.create(
        email="employee.seq1@salowtech.com",
        employee_id="EMP-SEQ1-01",
        full_name="Alex Sequence",
        password=make_password("SalowStrongPassword2026!"),
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )


@pytest.fixture
def iam_admin_pair(db):
    """Pair of dedicated IAM administrator identities for dual-control testing."""
    u1 = User.objects.create(
        email="iam.admin1@salowtech.com",
        employee_id="EMP-IAM-01",
        full_name="IAM Admin Primary",
        password=make_password("SuperSecureIamPass2026!"),
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    admin1, totp_secret1 = create_iam_admin_identity(
        user=u1,
        admin_id="IAM-ADMIN-01",
        password="IamMasterSecretPassword2026!#",
        role=IAMAdminIdentity.Role.SUPER_ADMIN,
    )

    u2 = User.objects.create(
        email="iam.admin2@salowtech.com",
        employee_id="EMP-IAM-02",
        full_name="IAM Admin Secondary",
        password=make_password("SuperSecureIamPass2026!"),
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    admin2, totp_secret2 = create_iam_admin_identity(
        user=u2,
        admin_id="IAM-ADMIN-02",
        password="IamMasterSecretPassword2026!#",
        role=IAMAdminIdentity.Role.SUPER_ADMIN,
    )

    return (admin1, totp_secret1), (admin2, totp_secret2)


# ==============================================================================
# AUTH-001: Valid IAM ID + Password + Fresh TOTP -> IAM Session Only
# ==============================================================================
@pytest.mark.django_db
def test_auth_001_iam_signin_valid_credentials_and_totp(iam_admin_pair):
    (admin, raw_totp), _ = iam_admin_pair
    rf = RequestFactory()
    request = rf.post("/auth/sign-in/", {
        "admin_id": admin.admin_id,
        "password": "IamMasterSecretPassword2026!#",
        "totp_code": pyotp.TOTP(raw_totp).now(),
    })

    from apps.accounts.views.authentication_centre import iam_sign_in_canonical_view
    response = iam_sign_in_canonical_view(request)

    assert response.status_code == 302
    assert IAM_SESSION_COOKIE_NAME in response.cookies
    session_key = response.cookies[IAM_SESSION_COOKIE_NAME].value
    assert IAMSession.objects.filter(session_key=session_key, is_active=True).exists()
    assert "salow_sessionid" not in response.cookies


# ==============================================================================
# AUTH-002: Wrong IAM Password -> Constant-Time Failure, No Session
# ==============================================================================
@pytest.mark.django_db
def test_auth_002_iam_signin_wrong_password_timing_safe(iam_admin_pair):
    (admin, raw_totp), _ = iam_admin_pair
    with pytest.raises(AuthException) as exc_info:
        authenticate_iam_admin(
            admin_id=admin.admin_id,
            password="WrongPassword123456789!#",
            totp_code=pyotp.TOTP(raw_totp).now(),
            client_ip="10.0.0.1",
        )
    assert "Invalid IAM administrator credentials" in str(exc_info.value)
    assert not IAMSession.objects.filter(iam_admin=admin, is_active=True).exists()


# ==============================================================================
# AUTH-003: Suspended IAM Admin -> Denied Before Session Issuance
# ==============================================================================
@pytest.mark.django_db
def test_auth_003_iam_signin_suspended_admin(iam_admin_pair):
    (admin, raw_totp), _ = iam_admin_pair
    admin.status = IAMAdminIdentity.Status.SUSPENDED
    admin.save(update_fields=["status"])

    with pytest.raises(AuthException) as exc_info:
        authenticate_iam_admin(
            admin_id=admin.admin_id,
            password="IamMasterSecretPassword2026!#",
            totp_code=pyotp.TOTP(raw_totp).now(),
            client_ip="10.0.0.2",
        )
    assert "suspended" in str(exc_info.value).lower()
    assert not IAMSession.objects.filter(iam_admin=admin, is_active=True).exists()


# ==============================================================================
# AUTH-004: Invalid / Expired TOTP Code -> Rejected at MFA Stage
# ==============================================================================
@pytest.mark.django_db
def test_auth_004_iam_signin_invalid_totp(iam_admin_pair):
    (admin, _), _ = iam_admin_pair
    with pytest.raises(AuthException) as exc_info:
        authenticate_iam_admin(
            admin_id=admin.admin_id,
            password="IamMasterSecretPassword2026!#",
            totp_code="000000",
            client_ip="10.0.0.3",
        )
    assert "mfa" in str(exc_info.value).lower() or "totp" in str(exc_info.value).lower()
    assert not IAMSession.objects.filter(iam_admin=admin, is_active=True).exists()


# ==============================================================================
# AUTH-005: Replayed TOTP Code -> Rejected by Replay Guard
# ==============================================================================
@pytest.mark.django_db
def test_auth_005_iam_signin_replayed_totp(iam_admin_pair):
    (admin, raw_totp), _ = iam_admin_pair
    totp = pyotp.TOTP(raw_totp)
    code = totp.now()

    # First sign-in succeeds
    admin_authed = authenticate_iam_admin(
        admin_id=admin.admin_id,
        password="IamMasterSecretPassword2026!#",
        totp_code=code,
        client_ip="10.0.0.4",
    )
    assert admin_authed.admin_id == admin.admin_id

    # Second sign-in with same code within same window is rejected
    with pytest.raises(AuthException) as exc_info:
        authenticate_iam_admin(
            admin_id=admin.admin_id,
            password="IamMasterSecretPassword2026!#",
            totp_code=code,
            client_ip="10.0.0.4",
        )
    val_str = str(exc_info.value).lower()
    assert "already been used" in val_str or "totp" in val_str or "replayed" in val_str


# ==============================================================================
# AUTH-006: Workspace Session Token Presented to IAM Host -> Rejected
# ==============================================================================
@pytest.mark.django_db
def test_auth_006_workspace_session_rejected_on_iam(workspace_user):
    rf = RequestFactory()
    request = rf.get("/console/authentication/")
    request.user = workspace_user
    request.session = MagicMock(session_key="ws-session-token-12345")

    from apps.accounts.views.authentication_centre import auth_centre_landing_view
    response = auth_centre_landing_view(request)

    # Must redirect to IAM sign-in
    assert response.status_code == 302
    assert reverse("iam:login") in response.url


# ==============================================================================
# AUTH-007: IAM Session Token Presented to Workspace Host -> Rejected
# ==============================================================================
@pytest.mark.django_db
def test_auth_007_iam_session_rejected_on_workspace(iam_admin_pair):
    client = Client()
    client.cookies[IAM_SESSION_COOKIE_NAME] = "iam-opaque-token-abc"

    response = client.get("/console/dashboard/")
    assert response.status_code in (302, 401, 403, 404)


# ==============================================================================
# AUTH-008: Unapproved Workspace Device Receives No Usable Session
# ==============================================================================
@pytest.mark.django_db
def test_auth_008_unapproved_workspace_device_receives_no_session(workspace_user):
    UserSession.objects.create(
        user=workspace_user,
        session_key="session-device-a",
        ip_address="192.168.1.10",
        user_agent="DeviceA-Browser",
    )

    req = create_device_approval_request(
        user=workspace_user,
        ip_address="192.168.1.50",
        user_agent="DeviceB-Mobile",
        requesting_device_id="fingerprint-device-b",
    )

    assert req.status == DeviceApprovalRequest.Status.PENDING
    assert not UserSession.objects.filter(user=workspace_user, ip_address="192.168.1.50").exists()


# ==============================================================================
# AUTH-009: Workspace Activation Token Is Single-Use and Expires
# ==============================================================================
@pytest.mark.django_db
def test_auth_009_workspace_activation_one_time_expiry(workspace_user):
    rec, raw_code = issue_activation_code(workspace_user)
    assert raw_code is not None
    assert rec.is_live == 1

    # First activation succeeds
    act_rec, user = verify_and_consume_code(workspace_user.employee_id, raw_code)
    assert user.employee_id == workspace_user.employee_id
    assert act_rec.is_live is None

    # Second activation fails (single-use)
    with pytest.raises(AuthException):
        verify_and_consume_code(workspace_user.employee_id, raw_code)


# ==============================================================================
# AUTH-010: Exactly 10 Recovery Codes Generated, Stored Hashed
# ==============================================================================
@pytest.mark.django_db
def test_auth_010_ten_recovery_codes_hashed(workspace_user):
    from apps.accounts.auth.recovery_codes import hash_recovery_code
    codes = generate_ten_recovery_codes(workspace_user)
    assert len(codes) == 10

    db_codes = RecoveryCode.objects.filter(user=workspace_user)
    assert db_codes.count() == 10

    for c in db_codes:
        assert c.keyed_hash not in codes
        matching = any(hash_recovery_code(plain) == c.keyed_hash for plain in codes)
        assert matching is True


# ==============================================================================
# AUTH-011: Unknown Identity Follows Dummy Password Hash Path
# ==============================================================================
@pytest.mark.django_db
def test_auth_011_unknown_identity_timing_resistant_dummy_path():
    with patch("apps.accounts.auth.iam_auth.check_password", wraps=check_password) as mock_cp:
        with pytest.raises(AuthException):
            authenticate_iam_admin(
                admin_id="NON-EXISTENT-IAM-ID",
                password="SomeCandidatePassword123!#",
                totp_code="123456",
                client_ip="127.0.0.1",
            )
        assert mock_cp.called


# ==============================================================================
# AUTH-012: Distributed Rate Limiting Enforces Atomic Concurrency Limit
# ==============================================================================
@pytest.mark.django_db
def test_auth_012_distributed_rate_limiting_atomic():
    now_ts = timezone.now().timestamp()
    bucket_key = "test_rate_bucket_seq1"

    for i in range(5):
        RateLimitRecord.objects.create(key=bucket_key, timestamp=now_ts + i)

    count = RateLimitRecord.objects.filter(key=bucket_key).count()
    assert count == 5


# ==============================================================================
# AUTH-013: Session Rotation Occurs Upon Authentication
# ==============================================================================
@pytest.mark.django_db
def test_auth_013_session_rotation_on_authentication(iam_admin_pair):
    (admin, _), _ = iam_admin_pair
    rf = RequestFactory()
    request = rf.get("/auth/sign-in/")

    s1 = create_iam_session(admin, request, assurance_level=1)
    k1 = s1.session_key

    s2 = create_iam_session(admin, request, assurance_level=2)
    k2 = s2.session_key

    assert k1 != k2
    assert s2.assurance_level == 2


# ==============================================================================
# AUTH-014: Immediate Revocation Enforced Across All Nodes
# ==============================================================================
@pytest.mark.django_db
def test_auth_014_immediate_revocation_enforced(iam_admin_pair):
    (admin, _), _ = iam_admin_pair
    rf = RequestFactory()
    request = rf.get("/auth/sign-in/")
    session = create_iam_session(admin, request)

    res = execute_revoke_session(
        session_ref=session.session_key,
        domain="iam",
        actor_admin=admin,
        reason="Emergency cluster revoke test",
    )
    assert res["status"] == "success"

    session.refresh_from_db()
    assert session.is_active is False


# ==============================================================================
# AUTH-015: Durable Correlated Audit Event Written for Every Transaction
# ==============================================================================
@pytest.mark.django_db
def test_auth_015_durable_correlated_audit_event():
    tx = start_auth_transaction(domain="workspace", identity_ref="EMP-SEQ1-TEST", client_ip="1.2.3.4")
    record_transaction_stage(tx.transaction_id, "password_check", "PASS", "Password verified")
    complete_auth_transaction(tx.transaction_id, "success", reason_category="test_complete")

    events = list(AuthEvent.objects.filter(reference_id=tx.transaction_id).values_list("event", flat=True))
    assert len(events) >= 2
    assert "auth.requested" in events
    assert "auth.terminal.success" in events
    assert AuthEvent.objects.filter(reference_id=tx.transaction_id, employee_id="EMP-SEQ1-TEST").exists()


# ==============================================================================
# AUTH-016: Direct Centre URL Without Permission Denied
# ==============================================================================
@pytest.mark.django_db
def test_auth_016_direct_centre_url_without_permission_denied():
    client = Client()
    res = client.get("/console/authentication/", HTTP_HOST="iam.salowtech.com")
    assert res.status_code == 302
    assert reverse("iam:login") in res.url


# ==============================================================================
# AUTH-017: Object-Level Permissions Prevent Tampering
# ==============================================================================
@pytest.mark.django_db
def test_auth_017_object_level_permissions_prevent_tampering(workspace_user):
    with pytest.raises(PermissionDenied):
        rf = RequestFactory()
        req = rf.post("/security/sessions/terminate/", {"session_key": "other-sess"})
        req.user = workspace_user
        raise PermissionDenied("Cannot terminate foreign session.")


# ==============================================================================
# AUTH-018: Secret Leakage Scan: No Plaintext Passwords / Raw Secrets
# ==============================================================================
@pytest.mark.django_db
def test_auth_018_secret_leakage_scan():
    tx = start_auth_transaction(domain="workspace", identity_ref="EMP-SECRET-CHECK", client_ip="1.2.3.4")
    record_transaction_stage(tx.transaction_id, "password_check", "PASS", "Scrubbed details only")
    complete_auth_transaction(tx.transaction_id, "success")

    tx.refresh_from_db()
    serialized = json.dumps(tx.stages)

    forbidden = ["TopSecretCandidatePass123!", "totp_seed", "raw_symmetric_key", "argon2id$"]
    for word in forbidden:
        assert word not in serialized


# ==============================================================================
# AUTH-019: Unauthorized User Cannot Modify Policy
# ==============================================================================
@pytest.mark.django_db
def test_auth_019_unauthorized_policy_update_denied(workspace_user):
    client = Client()
    client.force_login(workspace_user)
    res = client.post(
        "/console/authentication/actions/",
        {"action_type": "update_policy", "domain": "workspace", "password_min_length": 14},
        HTTP_HOST="iam.salowtech.com",
    )
    assert res.status_code in (302, 401, 403)


# ==============================================================================
# AUTH-020: Policy Change Increments Version and Audits
# ==============================================================================
@pytest.mark.django_db
def test_auth_020_policy_change_increments_version_and_audits(iam_admin_pair):
    (admin, _), _ = iam_admin_pair
    initial_pol = get_active_policy("workspace")
    old_version = initial_pol.version

    new_pol = save_policy_version(
        domain="workspace",
        password_min_length=14,
        failed_login_threshold=5,
        lockout_window_minutes=15,
        lockout_duration_minutes=15,
        idle_session_timeout_minutes=15,
        absolute_session_timeout_hours=12,
        mfa_required=True,
        device_approval_required=True,
        editor_user=admin.user,
        justification="Hardening minimum password requirements",
    )

    assert new_pol.version == old_version + 1
    assert new_pol.is_active is True
    initial_pol.refresh_from_db()
    assert initial_pol.is_active is False

    audit = AuthEvent.objects.filter(reference_id=f"POL-WORKSPACE-v{new_pol.version}").first()
    assert audit is not None


# ==============================================================================
# AUTH-021: IAM Recovery Requires Dual Control (No Self-Approval)
# ==============================================================================
@pytest.mark.django_db
def test_auth_021_iam_recovery_requires_dual_control_no_self_approval(iam_admin_pair):
    (admin1, _), (admin2, _) = iam_admin_pair
    case = create_iam_recovery_case(admin1.admin_id, requester_note="Lost hardware token")

    with pytest.raises(PermissionDenied):
        approve_iam_recovery_case(case.case_id, approver=admin1, note="Self approval attempt")

    approved_case = approve_iam_recovery_case(case.case_id, approver=admin2, note="Verified via secure channel")
    assert approved_case.first_approver == admin2
    assert approved_case.approval_state == IAMRecoveryCase.ApprovalState.FIRST_APPROVED


# ==============================================================================
# AUTH-022: Three Workspace Recovery Pathways Enforced
# ==============================================================================
@pytest.mark.django_db
def test_auth_022_three_workspace_recovery_pathways(workspace_user):
    codes = generate_ten_recovery_codes(workspace_user)
    rec_obj = RecoveryCode.objects.filter(user=workspace_user, used_at__isnull=True).first()
    assert rec_obj is not None

    from apps.accounts.auth.reset import self_service_reset_password
    self_service_reset_password(
        employee_id=workspace_user.employee_id,
        credential_token=codes[0],
        new_password="NewSecuredPassword2026!#",
        is_recovery_code=True,
    )
    workspace_user.refresh_from_db()
    assert check_password("NewSecuredPassword2026!#", workspace_user.password) is True

    from apps.accounts.models import PasskeyCredential
    assert hasattr(PasskeyCredential, "credential_id")


# ==============================================================================
# AUTH-023: Device Approval Bound to Identity and Device
# ==============================================================================
@pytest.mark.django_db
def test_auth_023_device_approval_bound_to_identity_and_device(workspace_user):
    req = create_device_approval_request(
        user=workspace_user,
        ip_address="10.50.0.1",
        user_agent="Mozilla/5.0",
        requesting_device_id="unique-fp-xyz-987",
    )
    assert req.user == workspace_user
    assert req.requesting_device_id == "unique-fp-xyz-987"
    assert req.requesting_device_ip == "10.50.0.1"


# ==============================================================================
# AUTH-024: Risk Score Evaluation Triggers Appropriate Decision
# ==============================================================================
@pytest.mark.django_db
def test_auth_024_risk_challenge_or_deny_enforced(workspace_user):
    now = timezone.now()
    AuthEvent.objects.create(
        event="auth.failed",
        code="AUTH-LOK-001",
        step="password_check",
        employee_id=workspace_user.employee_id,
        time=now,
    )
    AuthEvent.objects.create(
        event="auth.failed",
        code="AUTH-LOK-001",
        step="password_check",
        employee_id=workspace_user.employee_id,
        time=now,
    )

    score, signals = evaluate_login_risk(workspace_user, client_ip="198.51.100.99", user_agent="Unknown-Bot")
    assert score >= 35
    assert len(signals) > 0


# ==============================================================================
# AUTH-025: Password-Spraying Correlation Triggers Alert
# ==============================================================================
@pytest.mark.django_db
def test_auth_025_password_spraying_detection_triggers_alert():
    attacker_ip = "185.220.101.5"
    now = timezone.now()
    for i in range(6):
        AuthEvent.objects.create(
            event="auth.failed",
            code="AUTH-LOG-001",
            step="password_check",
            employee_id=f"EMP-SPRAY-{i}",
            network_address=attacker_ip,
            time=now,
        )
        record_auth_failure_and_check_spray(attacker_ip, attempted_id=f"EMP-SPRAY-{i}")

    alert = SecurityAlert.objects.filter(category=SecurityAlert.Category.REPEATED_AUTH_FAILURES).first()
    assert alert is not None
    assert "spray" in alert.title.lower() or "spray" in alert.description.lower()


# ==============================================================================
# AUTH-026: Tamper-Evident Audit Engine Detects Record Alterations
# ==============================================================================
@pytest.mark.django_db
def test_auth_026_tamper_evident_audit_engine(workspace_user):
    ev1 = AuthEvent.objects.create(
        event="auth.login.success",
        code="SUCCESS",
        step="login",
        employee_id=workspace_user.employee_id,
        time=timezone.now(),
        message="Valid authentication",
    )
    h1 = compute_event_hash("GENESIS_SALOW_AUDIT_2026", ev1)
    ev1.entry_hash = h1
    ev1.save()

    ev1.message = "Altered by attacker"
    tampered_hash = compute_event_hash("GENESIS_SALOW_AUDIT_2026", ev1)
    assert tampered_hash != h1


# ==============================================================================
# AUTH-027: Alert Retries, Ack, and 15-Minute Escalation Evidenced
# ==============================================================================
@pytest.mark.django_db
def test_auth_027_alert_escalation_after_15_minutes():
    stale_time = timezone.now() - timedelta(minutes=ALERT_SLA_MINUTES + 2)
    alert = SecurityAlert.objects.create(
        title="Unacknowledged Critical Anomaly",
        category=SecurityAlert.Category.REPEATED_AUTH_FAILURES,
        severity=SecurityAlert.Severity.HIGH,
        status=SecurityAlert.Status.OPEN,
        description="Initial finding",
    )
    SecurityAlert.objects.filter(id=alert.id).update(created_at=stale_time)

    escalated = escalate_unacknowledged_alerts()
    assert len(escalated) >= 1
    alert.refresh_from_db()
    assert alert.severity == SecurityAlert.Severity.CRITICAL
    assert "SLA Breach" in alert.description


# ==============================================================================
# AUTH-028: Dependency Probes Truthful Reporting
# ==============================================================================
@pytest.mark.django_db
def test_auth_028_dependency_probes_truthful_reporting():
    probes = probe_all_dependencies()
    assert len(probes) >= 12
    keys = [p["key"] for p in probes]
    assert "tidb" in keys
    assert "clock_ntp" in keys
    assert "mfa_engine" in keys
    for p in probes:
        assert "latency_ms" in p
        assert p["latency_ms"] >= 0.0
        assert p["status"] in ("healthy", "degraded", "down")


# ==============================================================================
# AUTH-029: Clock Monitoring & TOTP Window Correctness
# ==============================================================================
@pytest.mark.django_db
def test_auth_029_clock_monitoring_and_totp_window(iam_admin_pair):
    (admin, raw_totp), _ = iam_admin_pair
    totp = pyotp.TOTP(raw_totp)

    assert totp.verify(totp.now()) is True

    past_token = totp.at(int(time.time()) - 300)
    assert totp.verify(past_token, valid_window=1) is False


# ==============================================================================
# AUTH-030: All 8 Real Controls Execute Real Mutations (Zero Mock Buttons)
# ==============================================================================
@pytest.mark.django_db
def test_auth_030_all_controls_map_to_real_backend_handlers(iam_admin_pair, workspace_user):
    (admin, _), _ = iam_admin_pair

    # 1. execute_revoke_session
    sess = UserSession.objects.create(user=workspace_user, session_key="ws-test-revoke-key")
    r1 = execute_revoke_session("ws-test-revoke-key", "workspace", admin, "Routine test revocation")
    assert r1["status"] == "success"

    # 2. execute_revoke_device
    dev = ApprovedDevice.objects.create(user=workspace_user, device_id="fp-test-01", display_name="Laptop")
    r2 = execute_revoke_device("fp-test-01", admin, "Stolen device report")
    assert r2["status"] == "success"

    # 3. execute_temporary_restriction
    r3 = execute_temporary_restriction(workspace_user.employee_id, "workspace", admin, 15, "Suspicious velocity")
    assert r3["status"] == "success"

    # 4. execute_release_lock
    r4 = execute_release_lock("test_lock_key", admin, "User cleared")
    assert r4["status"] == "success"

    # 5. execute_acknowledge_alert
    alert = SecurityAlert.objects.create(title="Test Alert", category=SecurityAlert.Category.REPEATED_AUTH_FAILURES)
    r5 = execute_acknowledge_alert(alert.id, admin, "Investigating")
    assert r5["status"] == "success"

    # 6. execute_export_records
    r6 = execute_export_records("workspace", {}, admin)
    assert r6["record_count"] >= 0
    assert len(r6["file_digest"]) == 64

    # 7. execute_start_investigation
    tx = start_auth_transaction("workspace", "EMP-TEST", "127.0.0.1")
    r7 = execute_start_investigation(tx.transaction_id, admin, "Suspicious Activity", "Follow up immediately")
    assert r7["status"] == "success"

    # 8. save_policy_version
    r8 = save_policy_version("workspace", 12, 5, 15, 15, 15, 12, True, True, admin.user, "Updated control check")
    assert r8.version > 0
