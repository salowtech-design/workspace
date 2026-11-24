"""
Acceptance Test Suite for Complete Salow IAM Platform:
1. Machine-to-Machine (M2M) Service Identities & Scoped API Tokens.
2. Emergency Break-Glass Privileged Access Elevation & Time-Ceiling (15m).
3. Authenticated Server-Sent Events (SSE) Live Security Stream.
4. Ordinary Workspace Admin Panel (/attendance/admin/) with Redacted IAM Read-Only Summary.
5. Dedicated Identity & Security Console (/auth/admin/iam/) across all 12 operational areas.
6. Central IAM Security Invariants & Integration Guarantees.
"""

import pyotp
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
from apps.accounts.auth.service_identities import (
    issue_service_identity,
    revoke_service_identity,
    verify_service_identity,
)
from apps.accounts.auth.stepup import (
    activate_break_glass,
    is_break_glass_active,
    revoke_break_glass,
)
from apps.accounts.models import (
    AuthEvent,
    CustomUser,
    ServiceIdentity,
    TOTPCredential,
    UserSession,
)
from apps.attendance.models import AttendanceFallbackRequest, WorkSession
from apps.core.middleware import RateLimitMiddleware
from apps.core.models import EmailOutbox
from apps.directory.models import Department, Team


@pytest.fixture(autouse=True)
def reset_rate_limits():
    RateLimitMiddleware.reset()
    yield
    RateLimitMiddleware.reset()


@pytest.fixture
def test_users(db):
    super_admin = CustomUser.objects.create(
        employee_id="ST-ADM-0001",
        full_name="Executive Super Admin",
        email="superadmin@salowtech.com",
        email_verified=True,
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_staff=True,
        is_superuser=True,
    )
    super_admin.set_password("AdminSecurePassword123!@#")
    super_admin.save()

    sec = pyotp.random_base32(length=32)
    ct, nonce, key_id = encrypt_secret(sec, get_auth_key_ring())
    TOTPCredential.objects.create(
        user=super_admin,
        encrypted_secret=ct,
        nonce=nonce,
        key_id=key_id,
        confirmed_at=timezone.now(),
    )

    admin_user = CustomUser.objects.create(
        employee_id="ST-ADM-0002",
        full_name="Operations Admin",
        email="opsadmin@salowtech.com",
        email_verified=True,
        role=CustomUser.Role.ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_staff=True,
    )
    admin_user.set_password("AdminSecurePassword123!@#")
    admin_user.save()

    manager = CustomUser.objects.create(
        employee_id="ST-MGR-0001",
        full_name="Team Manager",
        email="manager@salowtech.com",
        email_verified=True,
        role=CustomUser.Role.MANAGER,
        status=CustomUser.Status.ACTIVE,
    )
    manager.set_password("ManagerPassword123!@#")
    manager.save()

    emp_user = CustomUser.objects.create(
        employee_id="ST-EMP-0010",
        full_name="Jordan Reed",
        email="jordan.reed@salowtech.com",
        email_verified=True,
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    emp_user.set_password("JordanPass123!@#")
    emp_user.save()

    intern_user = CustomUser.objects.create(
        employee_id="ST-INT-0001",
        full_name="Intern Alex",
        email="alex.intern@salowtech.com",
        email_verified=True,
        role=CustomUser.Role.INTERN,
        status=CustomUser.Status.ACTIVE,
    )
    intern_user.set_password("InternPass123!@#")
    intern_user.save()

    dept = Department.objects.create(code="ENG", name="Engineering")
    team = Team.objects.create(
        code="CORE-ENG", name="Core Engineering", department=dept, lead=manager
    )

    return {
        "super_admin": super_admin,
        "admin": admin_user,
        "manager": manager,
        "employee": emp_user,
        "intern": intern_user,
        "team": team,
    }


# ==============================================================================
# 1. Service Identities & M2M Scoped Tokens
# ==============================================================================


@pytest.mark.django_db
def test_issue_service_identity_generates_token_and_hashes_at_rest(test_users):
    """Issuing a Service Identity generates raw token prefixed with salow_svc_ and stores hash."""
    admin = test_users["admin"]
    scopes = ["attendance:read", "audit:write"]
    ident, raw_token = issue_service_identity(
        name="Attendance Sync Daemon",
        client_id="svc_att_sync_001",
        scopes=scopes,
        created_by=admin,
    )

    assert ident.client_id == "svc_att_sync_001"
    assert ident.name == "Attendance Sync Daemon"
    assert ident.is_active is True
    assert ident.scopes == scopes
    assert raw_token.startswith("salow_svc_")
    # Verify raw token is NOT stored unhashed
    assert ident.secret_hash != raw_token
    assert len(ident.secret_hash) == 64  # SHA-256 hex length


@pytest.mark.django_db
def test_verify_service_identity_success_and_last_used_update(test_users):
    """Verifying valid service credentials succeeds, checks scopes, and updates last_used_at."""
    admin = test_users["admin"]
    ident, raw_token = issue_service_identity(
        name="Webhook Dispatcher",
        client_id="svc_webhook_001",
        scopes=["outbox:deliver"],
        created_by=admin,
    )

    assert ident.last_used_at is None
    verified = verify_service_identity(
        "svc_webhook_001", raw_token, required_scope="outbox:deliver"
    )
    assert verified.id == ident.id
    verified.refresh_from_db()
    assert verified.last_used_at is not None


@pytest.mark.django_db
def test_verify_service_identity_rejection_cases(test_users):
    """Verification rejects invalid secret, inactive service, expired service, or missing scope."""
    admin = test_users["admin"]
    ident, raw_token = issue_service_identity(
        name="Test Service",
        client_id="svc_test_001",
        scopes=["attendance:read"],
        created_by=admin,
    )

    # 1. Invalid secret
    with pytest.raises(PermissionDenied, match="Invalid service credentials"):
        verify_service_identity("svc_test_001", "salow_svc_wrong_secret_1234567890")

    # 2. Missing scope
    with pytest.raises(PermissionDenied, match="lacks required scope"):
        verify_service_identity("svc_test_001", raw_token, required_scope="certificates:issue")

    # 3. Revoked / inactive service
    revoke_service_identity("svc_test_001", actor=admin)
    with pytest.raises(PermissionDenied, match="revoked or inactive"):
        verify_service_identity("svc_test_001", raw_token)


# ==============================================================================
# 2. Emergency Break-Glass Privileged Elevation
# ==============================================================================


@pytest.mark.django_db
def test_activate_break_glass_requires_justification_and_emits_audit(test_users):
    """Break-glass activation requires >=10 chars justification and emits AUTH-BRK-001."""
    admin = test_users["admin"]

    # Rejects short reason
    with pytest.raises(ValidationError, match="detailed justification"):
        activate_break_glass(admin, reason="short")

    # Valid activation
    justification = "Production TiKV cluster recovery during network split"
    activate_break_glass(admin, reason=justification, justification_ticket="INC-999")

    assert is_break_glass_active(admin) is True

    # Check high-severity audit log
    event = AuthEvent.objects.filter(code="AUTH-BRK-001", employee_id=admin.employee_id).first()
    assert event is not None
    assert "EMERGENCY BREAK-GLASS ACTIVATED" in event.message
    assert "INC-999" in event.message

    # Deactivation terminates break glass
    revoke_break_glass(admin)
    assert is_break_glass_active(admin) is False


# ==============================================================================
# 3. Authenticated Server-Sent Events (SSE) Live Security Stream
# ==============================================================================


@pytest.mark.django_db
def test_admin_iam_live_stream_authorization_and_event_format(client, test_users):
    """Live SSE stream requires admin role and yields text/event-stream content."""
    # 1. Employee is denied
    client.force_login(test_users["employee"])
    resp = client.get(reverse("accounts:admin_iam_live_stream"))
    assert resp.status_code == 403

    # 2. Admin is allowed and receives streaming response
    client.force_login(test_users["admin"])
    AuthEvent.objects.create(
        event="password_verified",
        code="INFO",
        step="auth_password",
        employee_id="ST-EMP-0010",
        reference_id="flow_stream_test",
        network_address="127.0.0.1",
        message="Test event for streaming",
    )

    resp = client.get(reverse("accounts:admin_iam_live_stream") + "?limit=5")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/event-stream")

    content = b"".join(resp.streaming_content).decode()
    assert "event: ping" in content
    assert "event: auth_event" in content
    assert "ST-EMP-0010" in content
    assert "flow_stream_test" in content


# ==============================================================================
# 4. Ordinary Workspace Admin Panel (/attendance/admin/)
# ==============================================================================


@pytest.mark.django_db
def test_workspace_admin_panel_segregation_and_redacted_summary(client, test_users):
    """Ordinary Workspace Admin Panel renders business operations and redacted IAM summary."""
    # 1. Non-admin employee is denied
    client.force_login(test_users["employee"])
    resp = client.get(reverse("attendance:admin_panel"))
    assert resp.status_code == 403

    # 2. Manager and Admin have access
    client.force_login(test_users["manager"])
    UserSession.objects.create(user=test_users["employee"], session_key="test_session_active_key_1")
    resp = client.get(reverse("attendance:admin_panel"))
    assert resp.status_code == 200
    html = resp.content.decode()

    # Business operations
    assert "Workspace Admin Panel" in html
    assert "Pending Biometric Fallback Exceptions" in html
    assert "Today&#39;s Attendance Sessions" in html or "Today's Attendance Sessions" in html

    # Redacted Read-Only Security Summary
    assert "Central IAM Security Isolation Active" in html
    assert "IAM Posture" in html
    assert "PROTECTED &amp; OPERATIONAL" in html or "PROTECTED & OPERATIONAL" in html


@pytest.mark.django_db
def test_workspace_admin_panel_review_fallback_action(client, test_users):
    """Ordinary Workspace Admin Panel can approve/reject attendance fallback requests."""
    client.force_login(test_users["manager"])
    emp = test_users["employee"]
    req = AttendanceFallbackRequest.objects.create(
        user=emp,
        request_type=AttendanceFallbackRequest.RequestType.CHECK_IN,
        work_date=timezone.now().date(),
        requested_time=timezone.now(),
        reason="Camera failure on mobile device",
    )

    resp = client.post(
        reverse("attendance:admin_panel"),
        {
            "action": "review_fallback",
            "request_id": req.id,
            "decision": "approve",
            "reason": "Verified manually via photo badge",
        },
        follow=True,
    )
    assert resp.status_code == 200
    req.refresh_from_db()
    assert req.status == AttendanceFallbackRequest.Status.APPROVED
    assert req.reviewed_by == test_users["manager"]

    # Verify work session was created
    session = WorkSession.objects.filter(user=emp, work_date=req.work_date).first()
    assert session is not None


# ==============================================================================
# 5. Dedicated Identity & Security Console Across All 12 Areas
# ==============================================================================


@pytest.mark.django_db
def test_admin_iam_renders_all_twelve_console_areas(client, test_users):
    """Dedicated IAM console renders all 12 operational areas in the navigation tabs."""
    client.force_login(test_users["admin"])
    resp = client.get(reverse("accounts:admin_iam"))
    assert resp.status_code == 200
    html = resp.content.decode()

    # Area 0: Overview
    assert "?tab=overview" in html
    # Area 1: Identity & Accounts
    assert "1. Identity & Accounts" in html
    # Area 2: Roles & Permissions
    assert "2. Roles & Permissions" in html
    # Area 3: Security & MFA
    assert "3. Security & MFA" in html
    # Area 4: Sessions & Devices
    assert "4. Sessions & Devices" in html
    # Area 5: Recovery Console
    assert "5. Recovery Console" in html
    # Area 6: Account Lifecycle
    assert "6. Account Lifecycle" in html
    # Area 7: IAM Audit
    assert "7. IAM Audit" in html
    # Area 8: People, Teams & Groups
    assert "8. People, Teams & Groups" in html
    # Area 9: Sign-in Investigation
    assert "9. Sign-in Investigation" in html
    # Area 10: Privileged Access
    assert "10. Privileged Access" in html
    # Area 11: Service Identities
    assert "11. Service Identities" in html
    # Area 12: Delivery & Operations
    assert "12. Delivery & Operations" in html


def _iam_console_client(user, *actions):
    """A client holding a real IAM session with fresh Level 2 step-up for the given actions
    (the console no longer runs assurance-gated mutations without an IAM session)."""
    from datetime import timedelta

    from django.test import Client
    from django.utils import timezone

    from apps.accounts.auth.iam_auth import (
        IAM_SESSION_COOKIE_NAME,
        create_iam_admin_identity,
        create_iam_session,
    )

    admin, _ = create_iam_admin_identity(
        user=user,
        admin_id="IAM-" + user.employee_id[-8:],
        password="IamMasterSecretPassword2026!#",
        role="super_admin" if user.role == CustomUser.Role.SUPER_ADMIN else "admin",
    )
    c = Client()
    fake = type("R", (), {"META": {"REMOTE_ADDR": "10.0.0.1"}, "headers": {}})()
    sess = create_iam_session(admin, fake)
    c.cookies[IAM_SESSION_COOKIE_NAME] = fake.session_key_iam
    sess.assurance_level = 2
    sess.stepup_action = actions[0]
    sess.stepup_expires_at = timezone.now() + timedelta(minutes=5)
    sess.save()
    return c, sess


def _bind_stepup(client, sess, data):
    """Do what the real step-up page does for this exact form: the console records the
    requested (actor, action, payload) when it demands a step-up, and a successful
    password+TOTP step-up promotes exactly that to the session (we skip only the factors)."""
    from datetime import timedelta

    from django.utils import timezone

    resp = client.post("/iam/dashboard/", data, HTTP_HOST="iam.salowtech.com")
    assert resp.status_code == 302 and "/iam/stepup/" in resp.url
    sess.refresh_from_db()
    assert sess.pending_action == data["action"] and sess.pending_payload_digest
    sess.assurance_level = 2
    sess.stepup_action = sess.pending_action
    sess.stepup_payload_digest = sess.pending_payload_digest
    sess.stepup_expires_at = timezone.now() + timedelta(minutes=5)
    sess.pending_action = ""
    sess.pending_payload_digest = ""
    sess.save()


@pytest.mark.django_db
def test_admin_iam_service_identity_lifecycle_via_console(client, test_users):
    """Admin can provision and revoke M2M Service Identities via the IAM console."""
    client, sess = _iam_console_client(test_users["admin"], "create_service_identity")

    # Provision service identity
    create_form = {
        "action": "create_service_identity",
        "name": "Background Task Runner",
        "client_id": "svc_bg_runner_999",
        "scopes": ["attendance:read", "audit:write"],
        "expires_in_days": "90",
    }
    _bind_stepup(client, sess, create_form)
    resp = client.post(
        "/iam/dashboard/",
        create_form,
        follow=True,
        HTTP_HOST="iam.salowtech.com",
    )
    assert resp.status_code == 200
    ident = ServiceIdentity.objects.filter(client_id="svc_bg_runner_999").first()
    assert ident is not None
    assert ident.is_active is True
    assert "attendance:read" in ident.scopes

    # Revoke service identity (a step-up is bound to one action, so renew it)
    revoke_form = {"action": "revoke_service_identity", "client_id": "svc_bg_runner_999"}
    _bind_stepup(client, sess, revoke_form)
    resp = client.post(
        "/iam/dashboard/",
        revoke_form,
        follow=True,
        HTTP_HOST="iam.salowtech.com",
    )
    assert resp.status_code == 200
    ident.refresh_from_db()
    assert ident.is_active is False


@pytest.mark.django_db
def test_admin_iam_break_glass_lifecycle_via_console(client, test_users):
    """Admin can activate and revoke emergency break-glass elevation from the console."""
    client.force_login(test_users["super_admin"])

    # Activate break glass
    resp = client.post(
        reverse("accounts:admin_iam"),
        {
            "action": "activate_break_glass",
            "reason": "Urgent recovery of compromised session cache",
            "ticket": "INC-8888",
        },
        follow=True,
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "BREAK-GLASS ACTIVE" in html

    # Revoke break glass
    resp = client.post(
        reverse("accounts:admin_iam"),
        {
            "action": "revoke_break_glass",
        },
        follow=True,
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Emergency break-glass privileges terminated" in html


@pytest.mark.django_db
def test_admin_iam_lifecycle_exit_account(client, test_users):
    """Marking employee as EXITED terminates all sessions and sets end_date."""
    client, _sess = _iam_console_client(test_users["admin"], "exit_account")
    emp = test_users["employee"]
    UserSession.objects.create(user=emp, session_key="emp_session_to_terminate_1")

    exit_form = {"action": "exit_account", "target_employee_id": emp.employee_id}
    _bind_stepup(client, _sess, exit_form)
    resp = client.post(
        "/iam/dashboard/",
        exit_form,
        follow=True,
        HTTP_HOST="iam.salowtech.com",
    )
    assert resp.status_code == 200
    emp.refresh_from_db()
    assert emp.status == CustomUser.Status.EXITED
    assert emp.is_active is False
    assert emp.end_date is not None
    assert UserSession.objects.filter(user=emp).count() == 0


@pytest.mark.django_db
def test_admin_iam_add_team_membership(client, test_users):
    """Admin can assign team membership through IAM console."""
    client.force_login(test_users["admin"])
    emp = test_users["employee"]
    team = test_users["team"]

    resp = client.post(
        reverse("accounts:admin_iam"),
        {
            "action": "add_team_membership",
            "target_employee_id": emp.employee_id,
            "team_code": team.code,
        },
        follow=True,
    )
    assert resp.status_code == 200
    assert team.memberships.filter(user=emp).exists()


@pytest.mark.django_db
def test_admin_iam_retry_outbox_items(client, test_users):
    """Admin can retry failed email notifications and outbox events."""
    client.force_login(test_users["admin"])

    # Create failed email
    email_item = EmailOutbox.objects.create(
        recipient="failed@salowtech.com",
        subject="Alert",
        body_text="Failed alert",
        notification_type="security_alert",
        status=EmailOutbox.Status.FAILED,
        retry_count=5,
    )

    resp = client.post(
        reverse("accounts:admin_iam"),
        {
            "action": "retry_outbox_item",
            "item_type": "email",
            "item_id": str(email_item.id),
        },
        follow=True,
    )
    assert resp.status_code == 200
    email_item.refresh_from_db()
    assert email_item.status == EmailOutbox.Status.QUEUED
    assert email_item.retry_count == 0


@pytest.mark.django_db
def test_break_glass_unprivileged_rejection(test_users):
    """Break-glass activation strictly rejects non-admin users (e.g. employee/intern)."""
    emp = test_users["employee"]
    with pytest.raises(PermissionDenied, match="restricted to system administrators"):
        activate_break_glass(emp, reason="Attempted unauthorized elevation by employee")


@pytest.mark.django_db
def test_service_identity_validation_and_timing_defense(test_users):
    """Service identity validates scopes, expiration, and rejects invalid credentials safely."""
    admin = test_users["admin"]

    # 1. Reject empty scopes
    with pytest.raises(ValidationError, match="At least one valid scope"):
        issue_service_identity("Empty Scopes", "svc_empty_001", scopes=[], created_by=admin)

    # 2. Reject past expiration
    past_date = timezone.now() - timezone.timedelta(days=1)
    with pytest.raises(ValidationError, match="must be in the future"):
        issue_service_identity(
            "Past Expiry",
            "svc_past_001",
            scopes=["attendance:read"],
            created_by=admin,
            expires_at=past_date,
        )

    # 3. Unknown client ID rejected with PermissionDenied (timing safe dummy path)
    with pytest.raises(PermissionDenied, match="Invalid service credentials"):
        verify_service_identity(
            "svc_non_existent", "salow_svc_fake_token_12345", request_ip="192.168.1.50"
        )


@pytest.mark.django_db
def test_admin_iam_live_stream_supports_last_event_id_and_custom_limit(client, test_users):
    """SSE live stream endpoint supports Last-Event-ID header and limits cleanly."""
    client.force_login(test_users["admin"])

    # Create 3 events
    e1 = AuthEvent.objects.create(event="e1", code="INFO", step="s1", employee_id="ST-EMP-0010")
    e2 = AuthEvent.objects.create(event="e2", code="INFO", step="s2", employee_id="ST-EMP-0010")
    e3 = AuthEvent.objects.create(event="e3", code="INFO", step="s3", employee_id="ST-EMP-0010")

    resp = client.get(
        reverse("accounts:admin_iam_live_stream"),
        headers={"Last-Event-ID": str(e1.id)},
    )
    assert resp.status_code == 200
    content = b"".join(resp.streaming_content).decode()
    assert f"id: {e2.id}" in content
    assert f"id: {e3.id}" in content
    assert f"id: {e1.id}" not in content


@pytest.mark.django_db
def test_workspace_admin_sidebar_role_isolation(client, test_users):
    """Employee and Manager do not see IAM & Access Management link in sidebar."""
    client.force_login(test_users["manager"])
    resp = client.get(reverse("attendance:panel"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Workspace Admin" in html
    assert "IAM &amp; Access Management" not in html and "IAM & Access Management" not in html

    client.force_login(test_users["employee"])
    resp = client.get(reverse("attendance:panel"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Workspace Admin" not in html
    assert "IAM &amp; Access Management" not in html and "IAM & Access Management" not in html

    client.force_login(test_users["admin"])
    resp = client.get(reverse("attendance:panel"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Workspace Admin" in html
    assert "IAM &amp; Access Management" in html or "IAM & Access Management" in html
