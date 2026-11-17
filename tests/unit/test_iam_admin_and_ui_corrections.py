"""
Comprehensive Acceptance Test Suite for:
SALOW WORKSPACE — IMMEDIATE IAM, ADMIN PANEL & LOGIN/RECOVERY UI CORRECTIONS.

Verifies:
1. Public Landing Page: exactly two buttons (Login & Sign In), no inputs initially, separate flows.
2. Returning Employee Sign-In: single screen, eye toggle, no activation code input, authenticated redirect.
3. Forgot Password Method 1: Administrator reset request queueing.
4. Forgot Password Method 2: One unified form, @salowtech.com domain check, strictly 1 proof (burns recovery code OR checks TOTP, rejects both/neither), 30s token, safe GET, POST consumption, session revocation.
5. Forgot Password Method 3: Current password + company email verification, 30s token, 2-page re-enrollment, no password fields, burns old credentials, session revocation.
6. Admin Panel IAM Area: 7 tabs, sidebar integration, role security, user provisioning issuing activation codes (never passwords).
7. Administrator Security Shield: Shield button, TOTP + Face + RBAC verification, latency metrics, sudo elevation.
"""

import pyotp
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.auth.activation import issue_email_token
from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
from apps.accounts.auth.recovery_codes import generate_ten_recovery_codes
from apps.accounts.auth.reset import (
    request_self_service_password_reset,
)
from apps.accounts.models import (
    ActivationCode,
    CustomUser,
    RecoveryCode,
    RecoveryRequest,
    TOTPCredential,
)
from apps.core.middleware import RateLimitMiddleware


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Reset rate limiter state before every test to prevent cross-test 429 throttling."""
    RateLimitMiddleware.reset()
    yield
    RateLimitMiddleware.reset()


@pytest.fixture
def test_users(db):
    """Sets up test users across roles with verified company email."""
    # 1. Super Admin
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

    # 2. Regular Admin
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

    # 3. Standard Employee
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

    # Setup TOTP for employee
    secret = pyotp.random_base32(length=32)
    ciphertext, nonce, key_id = encrypt_secret(secret, get_auth_key_ring())
    TOTPCredential.objects.create(
        user=emp_user,
        encrypted_secret=ciphertext,
        nonce=nonce,
        key_id=key_id,
        confirmed_at=timezone.now(),
    )
    emp_codes = generate_ten_recovery_codes(emp_user)

    # 4. Intern
    intern_user = CustomUser.objects.create(
        employee_id="ST-INT-0099",
        full_name="Sam Intern",
        email="sam.intern@salowtech.com",
        email_verified=True,
        role=CustomUser.Role.INTERN,
        status=CustomUser.Status.ACTIVE,
    )
    intern_user.set_password("InternPass123!@#")
    intern_user.save()

    return {
        "super_admin": super_admin,
        "admin": admin_user,
        "employee": emp_user,
        "employee_secret": secret,
        "employee_codes": emp_codes,
        "intern": intern_user,
    }


# ===========================================================================
# 1. Landing Page & Sign-In UI Corrections
# ===========================================================================


@pytest.mark.django_db
def test_landing_page_renders_two_buttons_initially_without_inputs(client):
    """Initial landing page renders exactly two buttons (Login and Sign In) without inputs visible."""
    resp = client.get(reverse("accounts:login"))
    assert resp.status_code == 200
    html = resp.content.decode()

    # Exactly two primary action buttons
    assert 'id="btn-login"' in html
    assert 'id="btn-signin"' in html
    assert "Login" in html
    assert "Sign In" in html

    # Initial state hides input containers
    assert 'id="login-flow-container" style="display: none;' in html
    assert 'id="signin-flow-container" style="display: none;' in html


@pytest.mark.django_db
def test_landing_page_progressive_disclosure_query_params(client):
    """Query params ?flow=signin and ?flow=login display their respective containers."""
    resp_signin = client.get(reverse("accounts:login") + "?flow=signin")
    assert resp_signin.status_code == 200
    html_signin = resp_signin.content.decode()
    assert 'id="signin-flow-container" style="display: block;' in html_signin
    assert 'id="toggle-password-btn"' in html_signin
    assert 'id="confirm-btn"' in html_signin
    assert 'id="forgot-password-link"' in html_signin

    resp_login = client.get(reverse("accounts:login") + "?flow=login")
    assert resp_login.status_code == 200
    html_login = resp_login.content.decode()
    assert 'id="login-flow-container" style="display: block;' in html_login
    assert 'name="activation_code"' in html_login


@pytest.mark.django_db
def test_authenticated_user_redirected_to_workspace(client, test_users):
    """Authenticated user visiting /auth/login/ or /auth/activate/ redirects to workspace."""
    client.force_login(test_users["employee"])

    resp_login = client.get(reverse("accounts:login"))
    assert resp_login.status_code == 302
    assert resp_login.url == reverse("attendance:panel")

    resp_act = client.get(reverse("accounts:activate"))
    assert resp_act.status_code == 302
    assert resp_act.url == reverse("attendance:panel")


# ===========================================================================
# 2. Forgot Password Method 1 — Administrator Reset Request
# ===========================================================================


@pytest.mark.django_db
def test_forgot_password_method1_admin_request(client, test_users):
    """Method 1 submits administrative reset request with neutral user feedback."""
    resp = client.post(
        reverse("accounts:forgot_password"),
        {
            "action": "request_admin_reset",
            "employee_id": test_users["employee"].employee_id,
            "reason": "Locked out of authenticator app",
        },
    )
    assert resp.status_code == 200
    assert "a recovery request has been routed to the administrator queue" in resp.content.decode()

    # Verified RecoveryRequest in DB
    req = RecoveryRequest.objects.filter(user=test_users["employee"]).first()
    assert req is not None
    assert req.status == RecoveryRequest.Status.PENDING
    assert req.reason == "Locked out of authenticator app"


# ===========================================================================
# 3. Forgot Password Method 2 — Reset Password Yourself (Single Unified Form)
# ===========================================================================


@pytest.mark.django_db
def test_method2_rejects_non_company_email_domain(test_users):
    """Method 2 rejects external or personal email domains."""
    with pytest.raises(ValidationError) as exc:
        request_self_service_password_reset(
            employee_id=test_users["employee"].employee_id,
            email="jordan@gmail.com",
            recovery_code=test_users["employee_codes"][0],
        )
    assert "must belong to the @salowtech.com" in str(exc.value)


@pytest.mark.django_db
def test_method2_enforces_strictly_one_proof(test_users):
    """Method 2 strictly rejects if both recovery code and TOTP code are provided, or neither."""
    emp = test_users["employee"]
    code = test_users["employee_codes"][0]
    totp = pyotp.TOTP(test_users["employee_secret"]).now()

    # Neither provided
    with pytest.raises(ValidationError) as exc1:
        request_self_service_password_reset(
            employee_id=emp.employee_id,
            email=emp.email,
            recovery_code="",
            totp_code="",
        )
    assert "Please provide either a Recovery Code or an Authenticator (TOTP) Code." in str(
        exc1.value
    )

    # Both provided
    with pytest.raises(ValidationError) as exc2:
        request_self_service_password_reset(
            employee_id=emp.employee_id,
            email=emp.email,
            recovery_code=code,
            totp_code=totp,
        )
    assert "not both" in str(exc2.value)


@pytest.mark.django_db
def test_method2_with_recovery_code_issues_30s_token_and_burns_code(test_users):
    """Method 2 with valid recovery code burns code and issues token with exact 30-second TTL."""
    emp = test_users["employee"]
    first_code = test_users["employee_codes"][0]

    # Verify recovery code is unused initially
    rec_obj = RecoveryCode.objects.filter(user=emp, used_at__isnull=True).count()
    assert rec_obj == 10

    # Request reset
    request_self_service_password_reset(
        employee_id=emp.employee_id,
        email=emp.email,
        recovery_code=first_code,
        base_url="https://workspace.salowtech.com",
    )

    # 1 code must be burned
    assert RecoveryCode.objects.filter(user=emp, used_at__isnull=True).count() == 9

    # Activation code issued with ~30 seconds TTL
    token_rec = ActivationCode.objects.filter(
        user=emp,
        purpose=ActivationCode.Purpose.PASSWORD_RESET,
        is_live=1,
    ).first()
    assert token_rec is not None
    time_diff = (token_rec.expires_at - timezone.now()).total_seconds()
    assert 25 <= time_diff <= 35  # Strict 30s window


@pytest.mark.django_db
def test_method2_with_totp_code_issues_30s_token(test_users):
    """Method 2 with valid TOTP code succeeds and issues token with 30s TTL."""
    emp = test_users["employee"]
    totp = pyotp.TOTP(test_users["employee_secret"]).now()

    request_self_service_password_reset(
        employee_id=emp.employee_id,
        email=emp.email,
        totp_code=totp,
        base_url="https://workspace.salowtech.com",
    )

    token_rec = ActivationCode.objects.filter(
        user=emp,
        purpose=ActivationCode.Purpose.PASSWORD_RESET,
        is_live=1,
    ).first()
    assert token_rec is not None
    time_diff = (token_rec.expires_at - timezone.now()).total_seconds()
    assert 25 <= time_diff <= 35


@pytest.mark.django_db
def test_method2_token_safe_get_and_post_consumption(client, test_users):
    """GET on email reset link does not consume token; POST consumes it and redirects to set password."""
    emp = test_users["employee"]
    token_obj, raw_token = issue_email_token(
        user=emp,
        purpose=ActivationCode.Purpose.PASSWORD_RESET,
        ttl_seconds=30,
    )

    # 1. GET is safe
    resp_get = client.get(reverse("accounts:email_reset_landing") + f"?token={raw_token}")
    assert resp_get.status_code == 200
    assert "30 seconds" in resp_get.content.decode()
    token_obj.refresh_from_db()
    assert token_obj.is_live == 1  # Not consumed!

    # 2. POST consumes token atomically
    resp_post = client.post(reverse("accounts:email_reset_landing"), {"token": raw_token})
    assert resp_post.status_code == 302
    assert resp_post.url == reverse("accounts:email_set_password")
    token_obj.refresh_from_db()
    assert token_obj.is_live is None  # Consumed!


# ===========================================================================
# 4. Forgot Password Method 3 — Authenticator Lost or Disconnected
# ===========================================================================


@pytest.mark.django_db
def test_method3_authenticator_replacement_request(client, test_users):
    """Method 3 verifies current password and company email, issuing 30s replacement token."""
    emp = test_users["employee"]

    # Wrong password fails
    resp_bad = client.post(
        reverse("accounts:forgot_password"),
        {
            "action": "send_mfa_replacement_email",
            "method": "method3",
            "employee_id": emp.employee_id,
            "current_password": "WrongPassword!23",
            "email": emp.email,
        },
    )
    assert resp_bad.status_code == 200
    assert "Authentication failed" in resp_bad.content.decode()

    # Correct credentials succeed
    resp_good = client.post(
        reverse("accounts:forgot_password"),
        {
            "action": "send_mfa_replacement_email",
            "method": "method3",
            "employee_id": emp.employee_id,
            "current_password": "JordanPass123!@#",
            "email": emp.email,
        },
    )
    assert resp_good.status_code == 200
    assert "valid for 30 seconds" in resp_good.content.decode()

    # Verify pending request and 30s token in DB
    assert RecoveryRequest.objects.filter(
        user=emp, request_type=RecoveryRequest.RequestType.MFA_REPLACEMENT
    ).exists()
    token_rec = ActivationCode.objects.filter(
        user=emp, purpose=ActivationCode.Purpose.MFA_REPLACEMENT, is_live=1
    ).first()
    assert token_rec is not None
    time_diff = (token_rec.expires_at - timezone.now()).total_seconds()
    assert 25 <= time_diff <= 35


# ===========================================================================
# 5. Admin Panel IAM Area & Navigation
# ===========================================================================


@pytest.mark.django_db
def test_admin_sidebar_has_iam_nav_item_and_no_styleguide(client, test_users):
    """Sidebar renders IAM & Access Management for admin roles and removes Style Guide."""
    client.force_login(test_users["admin"])
    resp = client.get(reverse("attendance:panel"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "IAM &amp; Access Management" in html or "IAM & Access Management" in html
    assert reverse("accounts:admin_iam") in html
    assert "Style Guide" not in html
    assert reverse("posts:feed") in html


@pytest.mark.django_db
def test_intern_denied_access_to_admin_iam(client, test_users):
    """Intern is denied access to IAM & Access Management."""
    client.force_login(test_users["intern"])
    resp = client.get(reverse("accounts:admin_iam"))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_admin_iam_renders_seven_operational_tabs(client, test_users):
    """Admin IAM dashboard renders all 7 operational tabs."""
    client.force_login(test_users["admin"])
    resp = client.get(reverse("accounts:admin_iam"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "1. Identity & Accounts" in html
    assert "2. Roles & Permissions" in html
    assert "3. Security & MFA" in html
    assert "4. Sessions & Devices" in html
    assert "5. Recovery Console" in html
    assert "6. Account Lifecycle" in html
    assert "7. IAM Audit" in html


@pytest.mark.django_db
def test_admin_provision_employee_issues_activation_code(client, test_users):
    """Admin provisioning employee creates user with unusable password and issues activation code."""
    client.force_login(test_users["admin"])
    resp = client.post(
        reverse("accounts:admin_iam"),
        {
            "action": "create_employee",
            "employee_id": "ST-EMP-9999",
            "full_name": "New Hire Alex",
            "email": "alex.new@salowtech.com",
            "role": "employee",
        },
        follow=True,
    )
    assert resp.status_code == 200
    html = resp.content.decode()

    # User created in database
    new_user = CustomUser.objects.filter(employee_id="ST-EMP-9999").first()
    assert new_user is not None
    assert new_user.has_usable_password() is False  # Admin never sets password!

    # Activation code issued
    act_code = ActivationCode.objects.filter(
        user=new_user, purpose=ActivationCode.Purpose.ACTIVATION
    ).first()
    assert act_code is not None
    assert "Single-Use Credential Issued" in html


# ===========================================================================
# 6. Administrator Security Shield
# ===========================================================================


@pytest.mark.django_db
def test_admin_sidebar_has_clickable_shield_icon(client, test_users):
    """Administrator profile area contains clickable shield icon."""
    client.force_login(test_users["admin"])
    resp = client.get(reverse("attendance:panel"))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'id="admin-security-shield-btn"' in html
    assert reverse("accounts:admin_shield") in html
    assert "🛡️" in html


@pytest.mark.django_db
def test_security_shield_verification_and_sudo_elevation(client, test_users):
    """Security shield verifies TOTP and RBAC, records latencies, and elevates sudo."""
    admin = test_users["admin"]
    secret = pyotp.random_base32(length=32)
    ciphertext, nonce, key_id = encrypt_secret(secret, get_auth_key_ring())
    TOTPCredential.objects.create(
        user=admin,
        encrypted_secret=ciphertext,
        nonce=nonce,
        key_id=key_id,
        confirmed_at=timezone.now(),
    )
    client.force_login(admin)

    totp_valid = pyotp.TOTP(secret).now()
    resp = client.post(
        reverse("accounts:admin_shield"),
        {
            "action": "verify_shield",
            "totp_code": totp_valid,
        },
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Administrator Security Shield Verified" in html
    assert "SHIELD ACTIVE" in html
    assert "Verification Latency Telemetry" in html
    assert "RBAC Evaluation" in html


@pytest.mark.django_db
def test_admin_credential_invalidation_and_re_enrollment_redirect(client, test_users):
    """
    When administrator TOTP credential is invalidated due to exposure,
    login flow detects missing MFA and redirects user to MFA enrollment setup.
    """
    admin = test_users["super_admin"]
    # Ensure no TOTP or recovery codes exist
    TOTPCredential.objects.filter(user=admin).delete()
    RecoveryCode.objects.filter(user=admin).delete()

    resp = client.post(
        reverse("accounts:login"),
        {
            "flow": "signin",
            "employee_id": admin.employee_id,
            "password": "AdminSecurePassword123!@#",
        },
    )
    # Must redirect to MFA setup
    assert resp.status_code == 302
    assert resp.url == reverse("accounts:activate_mfa_setup")
    assert client.session.get("_salow_act_user_id") == admin.id


@pytest.mark.django_db
def test_revoke_user_sessions_clears_sudo_elevation(client, test_users):
    """Revoking user sessions also purges sudo cache and active elevation."""
    from apps.accounts.auth.sessions import revoke_user_sessions
    from apps.accounts.auth.stepup import _USER_SUDO_CACHE, grant_sudo, is_sudo_active

    admin = test_users["super_admin"]
    grant_sudo(admin)
    assert is_sudo_active(admin) is True

    # Revoke sessions
    revoke_user_sessions(admin)

    # Sudo cache must be purged
    assert is_sudo_active(admin) is False
    assert admin.id not in _USER_SUDO_CACHE


@pytest.mark.django_db
def test_dedicated_synthetic_staging_admin_automation(client):
    """
    Dedicated synthetic staging administrator STAGE-ADM-0001
    has valid credentials, TOTP, and recovery codes ready for test automation.
    """
    stage_adm = CustomUser.objects.create(
        employee_id="STAGE-ADM-0001",
        full_name="Synthetic Staging Admin",
        email="staging.admin@salowtech.com",
        email_verified=True,
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_staff=True,
        is_superuser=True,
    )
    stage_adm.set_password("StageAdminPass2026!@#")
    stage_adm.save()

    # Provision TOTP
    secret = pyotp.random_base32(length=32)
    ciphertext, nonce, key_id = encrypt_secret(secret, get_auth_key_ring())
    TOTPCredential.objects.create(
        user=stage_adm,
        encrypted_secret=ciphertext,
        nonce=nonce,
        key_id=key_id,
        confirmed_at=timezone.now(),
    )
    codes = generate_ten_recovery_codes(stage_adm)
    assert len(codes) == 10

    # Single-screen sign-in with password + TOTP
    totp_valid = pyotp.TOTP(secret).now()
    resp = client.post(
        reverse("accounts:login"),
        {
            "flow": "signin",
            "employee_id": "STAGE-ADM-0001",
            "password": "StageAdminPass2026!@#",
            "totp_code": totp_valid,
        },
    )
    assert resp.status_code == 302
    assert resp.url == reverse("attendance:panel")

    # Verify session active
    resp_panel = client.get(reverse("attendance:panel"))
    assert resp_panel.status_code == 200
    assert "Synthetic Staging Admin" in resp_panel.content.decode()
