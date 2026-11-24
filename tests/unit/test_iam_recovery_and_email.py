"""
Comprehensive Unit and Integration Test Suite for Salow Workspace Central IAM:
1. Method 1 — Administrator Recovery Request, Dual-Control Approval, Sudo Enforcement & Rejection
2. Method 2 — Reset Password Yourself (Route A: Recovery Code, Route B: TOTP, Route C: Email Link)
3. Method 3 — Authenticator Replacement (Password Auth, Email Token, QR Setup, 10 Codes, Dashboard Redirect)
4. Transactional Email Outbox & Background Worker (Encryption, Delivery, Retry, Payload Wiping)
5. Company Email Verification State & Domain Allow-List
6. Server-Side Session Invalidation on Credential Change
"""

import time
from unittest.mock import patch

import pyotp
import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.auth.activation import (
    issue_email_token,
)
from apps.accounts.auth.config import is_allowed_company_email
from apps.accounts.auth.mfa_totp import (
    decrypt_secret,
    get_auth_key_ring,
    get_or_create_pending_totp,
    verify_totp_code,
)
from apps.accounts.auth.recovery_codes import (
    generate_ten_recovery_codes,
)
from apps.accounts.auth.reset import (
    approve_administrator_reset,
    reject_administrator_reset,
    request_email_password_reset,
)
from apps.accounts.auth.stepup import elevate_sudo
from apps.accounts.models import (
    ActivationCode,
    CustomUser,
    RecoveryCode,
    RecoveryRequest,
    TOTPCredential,
    UserSession,
)
from apps.core.email_service import process_email_outbox, queue_email
from apps.core.middleware import RateLimitMiddleware
from apps.core.models import EmailOutbox


@pytest.fixture(autouse=True)
def clean_rate_limits():
    RateLimitMiddleware.reset()
    yield
    RateLimitMiddleware.reset()


@pytest.fixture
def superadmin_user(db):
    user = CustomUser.objects.create_user(
        employee_id="ST-ADM-0001",
        full_name="Chief Administrator",
        email="chief.admin@salowtech.com",
        password="SuperSalowPassword2026!",
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_staff=True,
        is_superuser=True,
    )
    user.email_verified = True
    user.save()
    return user


@pytest.fixture
def second_superadmin(db):
    user = CustomUser.objects.create_user(
        employee_id="ST-ADM-0002",
        full_name="Second Super Admin",
        email="second.admin@salowtech.com",
        password="SuperSalowPassword2026!",
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_staff=True,
        is_superuser=True,
    )
    user.email_verified = True
    user.save()
    return user


@pytest.fixture
def standard_employee(db):
    user = CustomUser.objects.create_user(
        employee_id="ST-EMP-2001",
        full_name="Standard Employee",
        email="emp2001@salowtech.com",
        password="EmployeePassword2026!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    user.email_verified = True
    user.save()

    secret, _, _ = get_or_create_pending_totp(user)
    totp = pyotp.TOTP(secret)
    verify_totp_code(user, totp.now(), allow_unconfirmed=True)
    cred = TOTPCredential.objects.get(user=user)
    cred.last_used_step = (int(time.time()) // 30) - 2
    cred.save(update_fields=["last_used_step"])

    generate_ten_recovery_codes(user)
    return user


# ===========================================================================
# 1. METHOD 1 — Request Administrator Reset & Approval Queue
# ===========================================================================


@pytest.mark.django_db
class TestRecoveryMethod1AdminReset:
    """Verifies Method 1 Request Administrator Reset flow, anti-enumeration, and dual-control."""

    def test_anti_enumeration_request_submission(self, client, standard_employee):
        # 1. Existing employee submits reset request
        resp = client.post(
            reverse("accounts:forgot_password"),
            {
                "action": "request_admin_reset",
                "method": "method1",
                "employee_id": standard_employee.employee_id,
                "reason": "Lost credentials during office relocation",
            },
        )
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "a recovery request has been routed to the administrator queue" in content

        # Verify RecoveryRequest record was created
        req = RecoveryRequest.objects.get(user=standard_employee)
        assert req.status == RecoveryRequest.Status.PENDING
        assert req.reason == "Lost credentials during office relocation"

        # Verify outbox notification was queued for admins
        assert EmailOutbox.objects.filter(
            notification_type="admin_reset_requested", status=EmailOutbox.Status.QUEUED
        ).exists()

        # 2. Non-existent employee submits reset request - receives identical neutral message
        resp_fake = client.post(
            reverse("accounts:forgot_password"),
            {
                "action": "request_admin_reset",
                "method": "method1",
                "employee_id": "ST-EMP-NONEXISTENT",
                "reason": "Intruder probing",
            },
        )
        assert resp_fake.status_code == 200
        content_fake = resp_fake.content.decode("utf-8")
        assert "a recovery request has been routed to the administrator queue" in content_fake

    def test_admin_console_requires_permission_and_sudo(
        self, client, standard_employee, superadmin_user
    ):
        # Standard employee access denied
        client.force_login(standard_employee)
        resp_denied = client.get(reverse("accounts:admin_recovery_console"))
        assert resp_denied.status_code == 403

        # Super admin without active sudo redirected to sudo elevation
        client.force_login(superadmin_user)
        resp_sudo = client.get(reverse("accounts:admin_recovery_console"))
        assert resp_sudo.status_code == 302
        assert reverse("accounts:sudo") in resp_sudo.url

        # Activate sudo elevation
        elevate_sudo(superadmin_user)
        resp_console = client.get(reverse("accounts:admin_recovery_console"))
        assert resp_console.status_code == 200
        content = resp_console.content.decode("utf-8")
        assert "IAM Administrator Recovery Queue" in content

    def test_admin_cannot_approve_own_request(self, superadmin_user):
        req = RecoveryRequest.objects.create(
            user=superadmin_user,
            request_type=RecoveryRequest.RequestType.ADMIN_PASSWORD_RESET,
            status=RecoveryRequest.Status.PENDING,
        )
        elevate_sudo(superadmin_user)
        with pytest.raises(PermissionDenied, match="cannot approve their own"):
            approve_administrator_reset(
                request_id=req.id,
                actor=superadmin_user,
                base_url="http://localhost:8000",
            )

    def test_superadmin_target_requires_dual_approval(self, superadmin_user, second_superadmin):
        target_sa = CustomUser.objects.create_user(
            employee_id="ST-ADM-0003",
            full_name="Third Super Admin",
            email="third.admin@salowtech.com",
            password="SuperSalowPassword2026!",
            role=CustomUser.Role.SUPER_ADMIN,
            status=CustomUser.Status.ACTIVE,
        )
        target_sa.email_verified = True
        target_sa.save()

        req = RecoveryRequest.objects.create(
            user=target_sa,
            request_type=RecoveryRequest.RequestType.ADMIN_PASSWORD_RESET,
            status=RecoveryRequest.Status.PENDING,
        )
        elevate_sudo(superadmin_user)

        # Fails without second approver
        with pytest.raises(ValidationError, match="requires approval from a second administrator"):
            approve_administrator_reset(
                request_id=req.id,
                actor=superadmin_user,
                second_approver=None,
                base_url="http://localhost:8000",
            )

        # Fails if second approver is same as first
        with pytest.raises(ValidationError, match="approval from a second administrator"):
            approve_administrator_reset(
                request_id=req.id,
                actor=superadmin_user,
                second_approver=superadmin_user,
                base_url="http://localhost:8000",
            )

        # Succeeds with two distinct super admins
        approved_req, reset_token = approve_administrator_reset(
            request_id=req.id,
            actor=superadmin_user,
            second_approver=second_superadmin,
            base_url="http://localhost:8000",
        )
        assert approved_req.status == RecoveryRequest.Status.APPROVED
        assert approved_req.approved_by == superadmin_user
        assert approved_req.second_approved_by == second_superadmin
        assert reset_token is not None

        # Verify email was dispatched with reset link
        assert EmailOutbox.objects.filter(
            notification_type="admin_reset_approved", recipient=target_sa.email
        ).exists()

    def test_admin_reject_request(self, superadmin_user, standard_employee):
        req = RecoveryRequest.objects.create(
            user=standard_employee,
            request_type=RecoveryRequest.RequestType.ADMIN_PASSWORD_RESET,
            status=RecoveryRequest.Status.PENDING,
        )
        elevate_sudo(superadmin_user)

        rejected = reject_administrator_reset(
            request_id=req.id,
            actor=superadmin_user,
            reason="Identity verification unconfirmed via HR channel.",
        )
        assert rejected.status == RecoveryRequest.Status.REJECTED
        assert rejected.rejection_reason == "Identity verification unconfirmed via HR channel."

        # Verify rejection email dispatched
        assert EmailOutbox.objects.filter(
            notification_type="admin_reset_rejected", recipient=standard_employee.email
        ).exists()


# ===========================================================================
# 2. METHOD 2 — Reset Password Yourself (Route A, B, C)
# ===========================================================================


@pytest.mark.django_db
class TestRecoveryMethod2ResetYourself:
    """Verifies Method 2 self-service proof routes and single-use protections."""

    def test_method2_totp_plus_email_then_link_resets_password(self, client, standard_employee):
        cred = TOTPCredential.objects.get(user=standard_employee)
        secret = decrypt_secret(cred.encrypted_secret, cred.nonce, cred.key_id, get_auth_key_ring())
        totp = pyotp.TOTP(secret)

        # Step 1: the documented form: employee id + company email + exactly one proof.
        resp1 = client.post(
            reverse("accounts:forgot_password"),
            {
                "action": "reset_password_yourself",
                "employee_id": standard_employee.employee_id,
                "email": standard_employee.email,
                "totp_code": totp.now(),
            },
        )
        assert resp1.status_code == 200
        assert "valid for exactly 30 seconds" in resp1.content.decode("utf-8")
        assert EmailOutbox.objects.filter(notification_type="email_reset_link").count() == 1

        # Step 2: the emailed link (raw token is only in the encrypted mail, so re-issue one).
        _, raw_token = issue_email_token(
            standard_employee, ActivationCode.Purpose.PASSWORD_RESET, ttl_seconds=30
        )
        landing = reverse("accounts:email_reset_landing")
        assert client.get(f"{landing}?token={raw_token}").status_code == 200
        resp_post = client.post(landing, {"token": raw_token})
        assert resp_post.url == reverse("accounts:email_set_password")

        # Step 3: set the new password.
        new_pw = "BrandNewSalowPassword2026!"
        resp2 = client.post(
            reverse("accounts:email_set_password"),
            {"new_password": new_pw, "confirm_password": new_pw},
        )
        assert resp2.status_code == 302
        assert resp2.url == reverse("accounts:login")
        standard_employee.refresh_from_db()
        assert standard_employee.check_password(new_pw)

    def test_route_b_totp_denied_for_super_admin(self, client, superadmin_user):
        superadmin_user.email_verified = True
        superadmin_user.save()
        secret, _, _ = get_or_create_pending_totp(superadmin_user)
        totp = pyotp.TOTP(secret)
        verify_totp_code(superadmin_user, totp.now(), allow_unconfirmed=True)

        resp = client.post(
            reverse("accounts:forgot_password"),
            {
                "action": "reset_password_yourself",
                "employee_id": superadmin_user.employee_id,
                "email": superadmin_user.email,
                "totp_code": totp.now(),
            },
        )
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "administrator dual-approval" in content
        assert not EmailOutbox.objects.filter(notification_type="email_reset_link").exists()

    def test_reset_link_is_crawler_safe_and_single_use(self, client, standard_employee):
        # 1. The old email-only request is no longer served: no token, no mail.
        resp_req = client.post(
            reverse("accounts:forgot_password"),
            {"action": "send_email_reset_link", "employee_id": standard_employee.employee_id},
        )
        assert resp_req.status_code == 200
        assert "not available" in resp_req.content.decode("utf-8")
        assert not ActivationCode.objects.filter(user=standard_employee).exists()
        assert not EmailOutbox.objects.filter(notification_type="email_reset_link").exists()

        # Issue raw token to simulate email URL
        rec, raw_token = issue_email_token(standard_employee, ActivationCode.Purpose.PASSWORD_RESET)

        reset_landing_url = reverse("accounts:email_reset_landing")

        # 2. GET request simulates automated email security scanner (MUST NOT CONSUME TOKEN)
        resp_get = client.get(f"{reset_landing_url}?token={raw_token}")
        assert resp_get.status_code == 200
        assert "Password Reset Verification" in resp_get.content.decode("utf-8")

        # Verify token is still live after GET
        rec.refresh_from_db()
        assert rec.is_live == 1
        assert rec.consumed_at is None

        # 3. POST request by human user consumes token atomically
        resp_post = client.post(reset_landing_url, {"token": raw_token})
        assert resp_post.status_code == 302
        assert resp_post.url == reverse("accounts:email_set_password")

        # Verify token is now consumed
        rec.refresh_from_db()
        assert rec.is_live is None
        assert rec.consumed_at is not None

        # 4. Second POST attempt fails (Single-use redemption guarantee)
        resp_post2 = client.post(reset_landing_url, {"token": raw_token})
        assert resp_post2.status_code == 302
        assert resp_post2.url == reverse("accounts:forgot_password")

    def test_unverified_email_cannot_use_email_recovery(self, client):
        unverified_emp = CustomUser.objects.create_user(
            employee_id="ST-EMP-UNVERIFIED",
            full_name="Unverified User",
            email="unverified@salowtech.com",
            password="SalowPassword2026!",
            status=CustomUser.Status.ACTIVE,
        )
        unverified_emp.email_verified = False
        unverified_emp.save()

        request_email_password_reset(
            employee_id=unverified_emp.employee_id, base_url="http://localhost:8000"
        )
        # Outbox should not contain reset link for unverified email
        assert not EmailOutbox.objects.filter(
            notification_type="email_reset_link_requested", recipient=unverified_emp.email
        ).exists()


# ===========================================================================
# 3. METHOD 3 — Recover Lost or Replaced Authenticator
# ===========================================================================


@pytest.mark.django_db
class TestRecoveryMethod3MfaReplacement:
    """Verifies Method 3 authenticator replacement journey, QR setup, and 10 fresh codes."""

    def test_mfa_replacement_full_journey(self, client, standard_employee):
        # 1. Invalid current password fails
        resp_bad = client.post(
            reverse("accounts:forgot_password"),
            {
                "action": "send_mfa_replacement_email",
                "method": "method3",
                "employee_id": standard_employee.employee_id,
                "current_password": "WrongPassword123!",
            },
        )
        assert resp_bad.status_code == 200
        assert "Authentication failed" in resp_bad.content.decode("utf-8")

        # 2. Valid password triggers replacement email
        resp_ok = client.post(
            reverse("accounts:forgot_password"),
            {
                "action": "send_mfa_replacement_email",
                "method": "method3",
                "employee_id": standard_employee.employee_id,
                "current_password": "EmployeePassword2026!",
                "email": standard_employee.email,
            },
        )
        assert resp_ok.status_code == 200
        assert (
            "a single-use authenticator replacement link has been sent"
            in resp_ok.content.decode("utf-8")
        )

        # 3. Generate raw token to test continuation views
        rec, raw_token = issue_email_token(
            standard_employee, ActivationCode.Purpose.MFA_REPLACEMENT
        )

        verify_url = reverse("accounts:mfa_replacement_verify")

        # GET is crawler-safe
        resp_get = client.get(f"{verify_url}?token={raw_token}")
        assert resp_get.status_code == 200
        assert "Authenticator Replacement" in resp_get.content.decode("utf-8")
        rec.refresh_from_db()
        assert rec.is_live == 1

        # POST consumes token and moves to setup
        resp_post = client.post(verify_url, {"token": raw_token})
        assert resp_post.status_code == 302
        assert resp_post.url == reverse("accounts:mfa_replacement_setup")

        # 4. MFA Replacement Setup Screen renders QR SVG and manual secret
        setup_url = reverse("accounts:mfa_replacement_setup")
        resp_setup = client.get(setup_url)
        assert resp_setup.status_code == 200
        content_setup = resp_setup.content.decode("utf-8")
        assert "<svg" in content_setup
        assert "Manual Setup Key" in content_setup

        # Generate TOTP code from pending credential
        import re as _re

        key_text = _re.search(r"((?:[A-Z2-7]{4} ){7}[A-Z2-7]{4})", content_setup)
        secret = key_text.group(1).replace(" ", "")
        totp = pyotp.TOTP(secret)

        # 5. Submit valid 6-digit TOTP code
        resp_setup_post = client.post(setup_url, {"code": totp.now()})
        assert resp_setup_post.status_code == 302
        assert resp_setup_post.url == reverse("accounts:mfa_replacement_recovery_codes")

        # 6. Recovery codes display screen
        codes_url = reverse("accounts:mfa_replacement_recovery_codes")
        resp_codes_get = client.get(codes_url)
        assert resp_codes_get.status_code == 200
        content_codes = resp_codes_get.content.decode("utf-8")
        assert "New Backup Recovery Codes" in content_codes
        assert "Copy All Codes" in content_codes
        assert "Download as .txt" in content_codes
        assert "confirmed_saved" in content_codes

        # 7. Unchecked confirmation rejected
        resp_codes_unconfirmed = client.post(codes_url, {})
        assert resp_codes_unconfirmed.status_code == 200
        assert "You must confirm that you have saved" in resp_codes_unconfirmed.content.decode(
            "utf-8"
        )

        # Reset client burst window before final step
        RateLimitMiddleware.reset()

        # 8. Checked confirmation logs user in and redirects to role dashboard
        resp_codes_confirmed = client.post(codes_url, {"confirmed_saved": "1"})
        assert resp_codes_confirmed.status_code == 302
        assert resp_codes_confirmed.url == reverse("attendance:panel")

        # Verify exactly 10 fresh recovery codes in database
        assert (
            RecoveryCode.objects.filter(user=standard_employee, used_at__isnull=True).count() == 10
        )
        # Verify authenticated session exists
        assert UserSession.objects.filter(user=standard_employee, mfa_verified=True).exists()


# ===========================================================================
# 4. TRANSACTIONAL EMAIL OUTBOX & DELIVERY WORKER
# ===========================================================================


@pytest.mark.django_db
class TestTransactionalEmailOutboxAndWorker:
    """Verifies transactional outbox encryption, dispatch, and delivery worker."""

    def test_outbox_queueing_encryption_and_delivery(self):
        # 1. Queue an email
        outbox_entry = queue_email(
            recipient="test.delivery@salowtech.com",
            subject="Salow Security Test Notification",
            body_text="Your security notification body with secret token XYZ123.",
            notification_type="test_event",
            sensitive_payload={"token": "XYZ123"},
        )
        assert outbox_entry.status == EmailOutbox.Status.QUEUED
        assert outbox_entry.encrypted_payload is not None

        # 2. Execute process_email_outbox command
        call_command("process_email_outbox")

        # 3. Verify email sent via Django test mailbox
        assert len(mail.outbox) == 1
        sent_mail = mail.outbox[0]
        assert sent_mail.to == ["test.delivery@salowtech.com"]
        assert sent_mail.subject == "Salow Security Test Notification"
        assert "XYZ123" in sent_mail.body

        # 4. Verify outbox record updated and payload wiped
        outbox_entry.refresh_from_db()
        assert outbox_entry.status == EmailOutbox.Status.ACCEPTED_BY_TRANSPORT
        assert outbox_entry.sent_at is not None
        assert outbox_entry.encrypted_payload is None or len(outbox_entry.encrypted_payload) == 0

    def test_delivery_retry_and_backoff_on_failure(self):
        outbox_entry = queue_email(
            recipient="failure.test@salowtech.com",
            subject="Simulated Failure Test",
            body_text="Test Body",
            notification_type="test_fail",
        )

        with patch(
            "django.core.mail.EmailMultiAlternatives.send",
            side_effect=Exception("SMTP Connection Refused"),
        ):
            processed, failed = process_email_outbox(batch_size=10)
            assert failed == 1

        outbox_entry.refresh_from_db()
        assert outbox_entry.status == EmailOutbox.Status.RETRY_PENDING
        assert outbox_entry.retry_count == 1
        assert outbox_entry.scheduled_at is not None
        assert "SMTP Connection Refused" in outbox_entry.last_error


# ===========================================================================
# 5. COMPANY EMAIL VERIFICATION & DOMAIN ALLOW-LIST
# ===========================================================================


@pytest.mark.django_db
class TestCompanyEmailVerification:
    """Verifies email verification token lifecycle and company domain allow-listing."""

    def test_company_email_domain_allowlist(self):
        assert is_allowed_company_email("employee@salowtech.com") is True
        assert is_allowed_company_email("admin@subdomain.salowtech.com") is True
        assert is_allowed_company_email("attacker@gmail.com") is False
        assert is_allowed_company_email("intruder@salowtech.org") is False
        assert is_allowed_company_email("fake-salowtech.com@external.com") is False

    def test_email_verification_token_flow(self, client, standard_employee):
        standard_employee.email_verified = False
        standard_employee.save()

        rec, raw_token = issue_email_token(
            standard_employee, ActivationCode.Purpose.EMAIL_VERIFICATION
        )

        verify_url = reverse("accounts:verify_email")

        # GET is safe (crawler cannot verify)
        resp_get = client.get(f"{verify_url}?token={raw_token}")
        assert resp_get.status_code == 200
        assert "Verify Company Email" in resp_get.content.decode("utf-8")
        standard_employee.refresh_from_db()
        assert standard_employee.email_verified is False

        # POST completes verification
        resp_post = client.post(verify_url, {"token": raw_token})
        assert resp_post.status_code == 302
        assert resp_post.url == reverse("accounts:login")

        standard_employee.refresh_from_db()
        assert standard_employee.email_verified is True


@pytest.mark.django_db
class TestLegacySingleFactorRecoveryActionsAreGone:
    """The documented Method 2 needs one proof AND the company email; older direct POSTs skipped that."""

    def _assert_no_side_effects(self, user, before_hash):
        user.refresh_from_db()
        assert user.password == before_hash
        assert not ActivationCode.objects.filter(user=user).exists()
        assert not EmailOutbox.objects.filter(notification_type="email_reset_link").exists()

    def test_totp_only_action_is_denied(self, client, standard_employee):
        cred = TOTPCredential.objects.get(user=standard_employee)
        secret = decrypt_secret(cred.encrypted_secret, cred.nonce, cred.key_id, get_auth_key_ring())
        before = standard_employee.password
        resp = client.post(
            reverse("accounts:forgot_password"),
            {
                "action": "verify_totp_code",
                "employee_id": standard_employee.employee_id,
                "totp_code": pyotp.TOTP(secret).now(),
            },
        )
        assert resp.status_code == 200
        assert "not available" in resp.content.decode("utf-8")
        assert "_salow_pw_reset_uid" not in client.session and not any(
            k for k in client.session.keys() if "reset" in k
        )
        self._assert_no_side_effects(standard_employee, before)

    def test_recovery_code_only_action_is_denied_and_code_not_burned(
        self, client, standard_employee
    ):
        from apps.accounts.models import RecoveryCode

        codes = generate_ten_recovery_codes(standard_employee)
        before = standard_employee.password
        live_before = RecoveryCode.objects.filter(
            user=standard_employee, used_at__isnull=True
        ).count()
        for action in ("verify_recovery_code",):
            resp = client.post(
                reverse("accounts:forgot_password"),
                {
                    "action": action,
                    "employee_id": standard_employee.employee_id,
                    "recovery_code": codes[0],
                },
            )
            assert resp.status_code == 200
            assert "not available" in resp.content.decode("utf-8")
        assert (
            RecoveryCode.objects.filter(user=standard_employee, used_at__isnull=True).count()
            == live_before
        )
        self._assert_no_side_effects(standard_employee, before)

    def test_email_only_action_is_denied(self, client, standard_employee):
        before = standard_employee.password
        resp = client.post(
            reverse("accounts:forgot_password"),
            {"action": "send_email_reset_link", "employee_id": standard_employee.employee_id},
        )
        assert "not available" in resp.content.decode("utf-8")
        self._assert_no_side_effects(standard_employee, before)

    def test_direct_password_reset_with_totp_is_denied(self, client, standard_employee):
        cred = TOTPCredential.objects.get(user=standard_employee)
        secret = decrypt_secret(cred.encrypted_secret, cred.nonce, cred.key_id, get_auth_key_ring())
        before = standard_employee.password
        new_pw = "DirectBypassPassword2026!"
        resp = client.post(
            reverse("accounts:forgot_password"),
            {
                "action": "redeem_code",
                "employee_id": standard_employee.employee_id,
                "code_type": "totp",
                "code_value": pyotp.TOTP(secret).now(),
                "new_password": new_pw,
                "confirm_password": new_pw,
            },
        )
        assert "not available" in resp.content.decode("utf-8")
        standard_employee.refresh_from_db()
        assert not standard_employee.check_password(new_pw)
        self._assert_no_side_effects(standard_employee, before)


@pytest.mark.django_db
class TestMethod3RequiresTheRegisteredCompanyEmail:
    def _post(self, client, user, email):
        data = {
            "action": "send_mfa_replacement_email",
            "employee_id": user.employee_id,
            "current_password": "EmployeePassword2026!",
        }
        if email is not None:
            data["email"] = email
        return client.post(reverse("accounts:forgot_password"), data)

    def _no_side_effects(self, user):
        from apps.accounts.models import RecoveryRequest

        assert not RecoveryRequest.objects.filter(user=user).exists()
        assert not ActivationCode.objects.filter(user=user).exists()
        assert EmailOutbox.objects.count() == 0

    def test_missing_wrong_and_foreign_domain_email_are_refused(self, client, standard_employee):
        for email in (None, "", "someone.else@salowtech.com", "emp2001@gmail.com"):
            resp = self._post(client, standard_employee, email)
            assert resp.status_code == 200
            assert "has been sent" not in resp.content.decode("utf-8")
            self._no_side_effects(standard_employee)

    def test_matching_registered_email_still_works(self, client, standard_employee):
        from apps.accounts.models import RecoveryRequest

        resp = self._post(client, standard_employee, standard_employee.email)
        assert "has been sent" in resp.content.decode("utf-8")
        assert RecoveryRequest.objects.filter(user=standard_employee).count() == 1
        assert EmailOutbox.objects.count() == 1


class TestAuthenticatorReplacementKeepsOldFactorUntilAcknowledged:
    """The old authenticator and old recovery codes stay valid until the new code is
    verified AND the new recovery codes are acknowledged (abandoning loses nothing)."""

    def _to_setup(self, client, user):
        rec, raw = issue_email_token(user, ActivationCode.Purpose.MFA_REPLACEMENT)
        url = reverse("accounts:mfa_replacement_verify")
        client.post(url, {"token": raw})
        page = client.get(reverse("accounts:mfa_replacement_setup")).content.decode()
        import re as _re

        return _re.search(r"((?:[A-Z2-7]{4} ){7}[A-Z2-7]{4})", page).group(1).replace(" ", "")

    def test_opening_setup_page_does_not_touch_the_old_authenticator(
        self, client, standard_employee
    ):
        before = TOTPCredential.objects.get(user=standard_employee)
        old_secret = decrypt_secret(
            before.encrypted_secret, before.nonce, before.key_id, get_auth_key_ring()
        )
        self._to_setup(client, standard_employee)
        after = TOTPCredential.objects.get(user=standard_employee)
        assert after.confirmed_at is not None
        assert (
            decrypt_secret(after.encrypted_secret, after.nonce, after.key_id, get_auth_key_ring())
            == old_secret
        )

    def test_abandoning_before_acknowledgement_keeps_old_factor_and_codes(
        self, client, standard_employee
    ):
        generate_ten_recovery_codes(standard_employee)
        old_hashes = set(
            RecoveryCode.objects.filter(user=standard_employee).values_list("keyed_hash", flat=True)
        )
        before = TOTPCredential.objects.get(user=standard_employee)
        old_secret = decrypt_secret(
            before.encrypted_secret, before.nonce, before.key_id, get_auth_key_ring()
        )
        new_secret = self._to_setup(client, standard_employee)
        client.post(
            reverse("accounts:mfa_replacement_setup"), {"code": pyotp.TOTP(new_secret).now()}
        )
        # user closes the page here: no acknowledgement
        cred = TOTPCredential.objects.get(user=standard_employee)
        assert (
            decrypt_secret(cred.encrypted_secret, cred.nonce, cred.key_id, get_auth_key_ring())
            == old_secret
        )
        assert old_hashes == set(
            RecoveryCode.objects.filter(user=standard_employee).values_list("keyed_hash", flat=True)
        )

    def test_wrong_code_does_not_advance(self, client, standard_employee):
        self._to_setup(client, standard_employee)
        resp = client.post(reverse("accounts:mfa_replacement_setup"), {"code": "000000"})
        assert resp.status_code == 200

    def test_acknowledgement_commits_new_factor_and_codes(self, client, standard_employee):
        generate_ten_recovery_codes(standard_employee)
        old_hashes = set(
            RecoveryCode.objects.filter(user=standard_employee).values_list("keyed_hash", flat=True)
        )
        new_secret = self._to_setup(client, standard_employee)
        client.post(
            reverse("accounts:mfa_replacement_setup"), {"code": pyotp.TOTP(new_secret).now()}
        )
        RateLimitMiddleware.reset()
        client.post(reverse("accounts:mfa_replacement_recovery_codes"), {"confirmed_saved": "1"})
        cred = TOTPCredential.objects.get(user=standard_employee)
        assert (
            decrypt_secret(cred.encrypted_secret, cred.nonce, cred.key_id, get_auth_key_ring())
            == new_secret
        )
        new_hashes = set(
            RecoveryCode.objects.filter(user=standard_employee).values_list("keyed_hash", flat=True)
        )
        assert len(new_hashes) == 10 and not (new_hashes & old_hashes)
