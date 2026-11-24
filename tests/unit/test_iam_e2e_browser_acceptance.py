"""
End-to-End Acceptance Test Suite for Salow Workspace IAM:
1. Rate-Limiting GET/POST segregation & Branded 429 HTML response
2. Landing Page UI: Exactly Two Primary Buttons (Login & Sign In) without passkeys
3. Flow A: First-Time Employee Activation -> Password -> TOTP -> 10 Recovery Codes -> Dashboard
4. Flow B: Returning Employee Sign-In on Single Screen (Employee ID + Password + TOTP -> Confirm)
5. Flow C: Forgot Password & Account Recovery (Recovery Code / Token / Passkey)
"""

import pyotp
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.auth.activation import issue_activation_code
from apps.accounts.auth.mfa_totp import (
    decrypt_secret,
    get_auth_key_ring,
    get_or_create_pending_totp,
    verify_totp_code,
)
from apps.accounts.auth.recovery_codes import (
    generate_ten_recovery_codes,
    hash_recovery_code,
)
from apps.accounts.models import CustomUser, RecoveryCode, TOTPCredential, UserSession
from apps.core.middleware import RateLimitMiddleware

User = get_user_model()


@pytest.fixture(autouse=True)
def clean_rate_limits():
    RateLimitMiddleware.reset()
    yield
    RateLimitMiddleware.reset()


@pytest.fixture
def active_user(db):
    import time

    user = CustomUser.objects.create_user(
        employee_id="ST-EMP-8801",
        full_name="Browser Test Employee",
        password="ValidSalowPassword2026!",
        status=CustomUser.Status.ACTIVE,
    )
    secret, _, _ = get_or_create_pending_totp(user)
    totp = pyotp.TOTP(secret)
    verify_totp_code(user, totp.now(), allow_unconfirmed=True)
    # Rewind last_used_step by 2 steps so current token isn't flagged as replay
    cred = TOTPCredential.objects.get(user=user)
    cred.last_used_step = (int(time.time()) // 30) - 2
    cred.save(update_fields=["last_used_step"])

    generate_ten_recovery_codes(user)
    return user


@pytest.mark.django_db
class TestRateLimitingSegregationAnd429:
    """Verifies unmetered GET page rendering and branded 429 response on POST burst abuse."""

    def test_unmetered_get_page_rendering(self, client):
        """GET requests to auth pages must NOT be throttled by sensitive quota."""
        endpoints = [
            reverse("accounts:activate"),
            reverse("accounts:login"),
            reverse("accounts:forgot_password"),
        ]
        for url in endpoints:
            for i in range(12):
                resp = client.get(url)
                assert resp.status_code == 200, (
                    f"GET {url} failed on request {i + 1} with status {resp.status_code}"
                )

    def test_sensitive_post_burst_triggers_branded_429(self, client):
        """Sensitive POST credential attempts must throttle after SENSITIVE_LIMIT with branded 429 template."""
        login_url = reverse("accounts:login")

        # Exceed sensitive limit of 5 requests
        for _ in range(RateLimitMiddleware.SENSITIVE_LIMIT):
            resp = client.post(
                login_url,
                {"employee_id": "ST-EMP-UNKNOWN", "password": "BadPassword123!"},
            )
            assert resp.status_code == 200  # Returns login page with error message

        # 6th request must trigger HTTP 429
        blocked_resp = client.post(
            login_url,
            {"employee_id": "ST-EMP-UNKNOWN", "password": "BadPassword123!"},
        )
        assert blocked_resp.status_code == 429
        assert blocked_resp.headers.get("Retry-After") == "60"
        content = blocked_resp.content.decode("utf-8")
        assert "Too Many Requests" in content
        assert "Rate limit exceeded" in content
        assert "countdown-timer" in content
        assert "Return to Sign In" in content

    def test_reset_ratelimit_management_command(self, client):
        """Executing 'python manage.py reset_ratelimit' clears 429 lockouts."""
        login_url = reverse("accounts:login")

        # Force rate limit lock
        for _ in range(RateLimitMiddleware.SENSITIVE_LIMIT + 1):
            client.post(login_url, {"employee_id": "ST-EMP-BLOCKED", "password": "Wrong!"})

        # Confirm locked
        locked = client.post(login_url, {"employee_id": "ST-EMP-BLOCKED", "password": "Wrong!"})
        assert locked.status_code == 429

        # Run reset command
        call_command("reset_ratelimit")

        # Confirm unlocked
        unlocked = client.post(login_url, {"employee_id": "ST-EMP-BLOCKED", "password": "Wrong!"})
        assert unlocked.status_code == 200


@pytest.mark.django_db
class TestLandingPageUI:
    """Verifies the Landing Page presents exactly two primary buttons without passkeys."""

    def test_landing_page_buttons_and_layout(self, client):
        resp = client.get(reverse("accounts:login"))
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")

        # Verify exactly two primary buttons for Login and Sign In
        assert 'id="btn-login"' in content
        assert 'id="btn-signin"' in content
        assert reverse("accounts:activate") in content

        # Verify passkey button is NOT rendered on initial landing page
        assert "passkey-btn" not in content
        assert "Sign in with Passkey" not in content

        # Verify single-screen returning user form inputs
        assert 'name="employee_id"' in content
        assert 'name="password"' in content
        assert 'name="totp_code"' in content

        # Verify Confirm button
        assert 'id="confirm-btn"' in content
        assert "Confirm" in content

        # Verify Forgot Password link immediately below Confirm
        assert 'id="forgot-password-link"' in content
        assert reverse("accounts:forgot_password") in content


@pytest.mark.django_db
class TestFlowAActivationWizard:
    """Verifies complete First-Time Activation Journey (Flow A)."""

    def test_complete_activation_flow(self, client):
        # 1. Administrator issues activation code for invited employee
        user = CustomUser.objects.create_user(
            employee_id="ST-NEW-1001",
            full_name="New Activated Engineer",
            status=CustomUser.Status.INVITED,
        )
        raw_code = issue_activation_code(user)

        # Step 1: Submit Employee ID and one-time activation code
        resp1 = client.post(
            reverse("accounts:activate"),
            {"employee_id": user.employee_id, "activation_code": raw_code},
        )
        assert resp1.status_code == 302
        assert resp1.url == reverse("accounts:activate_password")

        # Step 2: Set permanent Argon2id password
        new_password = "SalowMasterPassword2026!"
        resp2 = client.post(
            reverse("accounts:activate_password"),
            {"password": new_password, "confirm_password": new_password},
        )
        assert resp2.status_code == 302
        assert resp2.url == reverse("accounts:activate_mfa_setup")

        # Step 3: MFA enrollment screen renders pure SVG QR code and grouped secret
        resp3_get = client.get(reverse("accounts:activate_mfa_setup"))
        assert resp3_get.status_code == 200
        content3 = resp3_get.content.decode("utf-8")
        assert "<svg" in content3
        assert "Manual Entry Secret Key" in content3

        # Obtain secret and generate valid 6-digit TOTP
        cred = TOTPCredential.objects.get(user=user)
        secret = decrypt_secret(cred.encrypted_secret, cred.nonce, cred.key_id, get_auth_key_ring())
        totp = pyotp.TOTP(secret)
        current_token = totp.now()

        resp3_post = client.post(
            reverse("accounts:activate_mfa_setup"),
            {"code": current_token},
        )
        assert resp3_post.status_code == 302
        assert resp3_post.url == reverse("accounts:activate_recovery_codes")

        # Step 4: Display 10 recovery codes with Copy and Download actions
        resp4_get = client.get(reverse("accounts:activate_recovery_codes"))
        assert resp4_get.status_code == 200
        content4 = resp4_get.content.decode("utf-8")
        assert "copy-codes-btn" in content4
        assert "download-codes-btn" in content4
        assert "confirmed_saved" in content4

        # Confirm saved recovery codes
        resp4_post = client.post(
            reverse("accounts:activate_recovery_codes"),
            {"confirmed_saved": "1"},
        )
        assert resp4_post.status_code == 302
        assert resp4_post.url == reverse("attendance:panel")

        # Assert user is fully active and verified session exists
        user.refresh_from_db()
        assert user.status == CustomUser.Status.ACTIVE
        assert UserSession.objects.filter(user=user, mfa_verified=True).exists()

    def test_login_activation_with_initial_password(self, client):
        """Verifies that an unactivated employee can authenticate using their initial password on /auth/activate/."""
        CustomUser.objects.create_user(
            employee_id="ST-NEW-PW01",
            full_name="Initial Password Employee",
            password="initial_password_123",
            status=CustomUser.Status.INVITED,
        )
        resp = client.post(
            reverse("accounts:activate"),
            {"employee_id": "ST-NEW-PW01", "password": "initial_password_123"},
        )
        assert resp.status_code == 302
        assert resp.url == reverse("accounts:activate_mfa_setup")


@pytest.mark.django_db
class TestFlowBReturningEmployeeSignIn:
    """Verifies Flow B Returning Employee Single-Screen Authentication."""

    def test_single_screen_signin_with_totp(self, client, active_user):
        """Entering Employee ID + Password + TOTP on single screen logs in directly."""
        cred = TOTPCredential.objects.get(user=active_user)
        secret = decrypt_secret(cred.encrypted_secret, cred.nonce, cred.key_id, get_auth_key_ring())
        totp = pyotp.TOTP(secret)

        resp = client.post(
            reverse("accounts:login"),
            {
                "employee_id": active_user.employee_id,
                "password": "ValidSalowPassword2026!",
                "totp_code": totp.now(),
            },
        )
        assert resp.status_code == 302
        assert resp.url == reverse("attendance:panel")

        # Verify session is established and active
        assert UserSession.objects.filter(user=active_user, mfa_verified=True).exists()

    def test_single_screen_signin_with_recovery_code(self, client, active_user):
        """Entering Employee ID + Password + backup recovery code on single screen logs in directly."""
        codes = generate_ten_recovery_codes(active_user)
        recovery_code = codes[0]

        resp = client.post(
            reverse("accounts:login"),
            {
                "employee_id": active_user.employee_id,
                "password": "ValidSalowPassword2026!",
                "totp_code": recovery_code,
            },
        )
        assert resp.status_code == 302
        assert resp.url == reverse("attendance:panel")

        # Verify recovery code is marked used
        rc = RecoveryCode.objects.get(
            user=active_user, keyed_hash=hash_recovery_code(recovery_code)
        )
        assert rc.used_at is not None


@pytest.mark.django_db
class TestFlowCForgotPasswordRecovery:
    """Verifies Flow C Account Recovery (Method 2 Route A Two-Step Flow)."""

    def test_recovery_with_backup_code(self, client, active_user):
        codes = generate_ten_recovery_codes(active_user)
        raw_code = codes[0]

        # Step 1: documented Method 2 form: recovery code + company email, then the emailed link.
        active_user.email = "active.user.reset@salowtech.com"
        active_user.email_verified = True
        active_user.save()
        resp1 = client.post(
            reverse("accounts:forgot_password"),
            {
                "action": "reset_password_yourself",
                "employee_id": active_user.employee_id,
                "email": active_user.email,
                "recovery_code": raw_code,
            },
        )
        assert resp1.status_code == 200
        from apps.accounts.auth.activation import issue_email_token
        from apps.accounts.models import ActivationCode

        _, raw_token = issue_email_token(
            active_user, ActivationCode.Purpose.PASSWORD_RESET, ttl_seconds=30
        )
        resp_link = client.post(reverse("accounts:email_reset_landing"), {"token": raw_token})
        assert resp_link.url == reverse("accounts:email_set_password")

        # Step 2: Set new password on protected set password screen
        new_password = "ResetSalowPassword2026!"
        resp2 = client.post(
            reverse("accounts:email_set_password"),
            {
                "new_password": new_password,
                "confirm_password": new_password,
            },
        )
        assert resp2.status_code == 302
        assert resp2.url == reverse("accounts:login")

        # Verify password updated
        active_user.refresh_from_db()
        assert active_user.check_password(new_password)

        # Verify recovery code cannot be replayed
        resp_replay = client.post(
            reverse("accounts:forgot_password"),
            {
                "action": "reset_password_yourself",
                "employee_id": active_user.employee_id,
                "email": active_user.email,
                "recovery_code": raw_code,
            },
        )
        assert resp_replay.status_code == 200
        assert "has been sent" not in resp_replay.content.decode("utf-8")
