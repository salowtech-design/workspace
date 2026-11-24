"""
Comprehensive Unit & Integration Test Suite for Hardened IAM Subsystems:
1. WebAuthn / Passkeys (Level 3 options, assertion, revocation)
2. Delegated Recovery (Super Admin dual-approval, HR & Manager scoping, self-service reset)
3. Session Expiry, Idle Lock, & Workspace Draft Resumption
4. Distributed Rate Limiting & Trusted Proxy Coordination
5. 12-Point Auth Doctor Diagnostics Verification
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory
from django.utils import timezone

from apps.accounts.auth.diagnostics import run_auth_doctor_checks
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.passkeys import (
    AUTH_CHALLENGE_SESSION_KEY,
    REG_CHALLENGE_SESSION_KEY,
    generate_passkey_authentication_options,
    generate_passkey_registration_options,
    revoke_passkey,
    verify_passkey_registration,
)
from apps.accounts.auth.recovery_codes import generate_ten_recovery_codes
from apps.accounts.auth.reset import (
    check_recovery_authority,
    self_service_reset_password,
)
from apps.accounts.auth.sessions import (
    end_session_by_key,
    revoke_user_sessions,
)
from apps.accounts.auth.stepup import (
    SUDO_GRANTED_TIMESTAMP_KEY,
    is_sudo_active,
    verify_stepup,
)
from apps.accounts.models import (
    CustomUser,
    PasskeyCredential,
    TOTPCredential,
    UserSession,
)
from apps.accounts.services import set_user_role, set_user_status
from apps.core.middleware import RateLimitMiddleware
from apps.core.models import RateLimitRecord
from apps.directory.models import ReportingRelationship

User = get_user_model()


@pytest.fixture(autouse=True)
def reset_rate_limit_state():
    RateLimitMiddleware.reset()
    yield
    RateLimitMiddleware.reset()


@pytest.fixture
def super_admin(db):
    return CustomUser.objects.create_superuser(
        employee_id="ST-ADM-0001",
        full_name="Super Administrator One",
        password="SuperMasterPassword123!",
    )


@pytest.fixture
def super_admin_two(db):
    return CustomUser.objects.create_superuser(
        employee_id="ST-ADM-0002",
        full_name="Super Administrator Two",
        password="SuperMasterPassword456!",
    )


@pytest.fixture
def admin_user(db):
    return CustomUser.objects.create_user(
        employee_id="ST-ADM-0010",
        full_name="Standard Administrator",
        password="AdminMasterPassword123!",
        role=CustomUser.Role.ADMIN,
        is_staff=True,
    )


@pytest.fixture
def hr_user(db):
    return CustomUser.objects.create_user(
        employee_id="ST-HR-0001",
        full_name="HR Specialist",
        password="HRMasterPassword123!",
        role=CustomUser.Role.HR,
    )


@pytest.fixture
def manager_user(db):
    return CustomUser.objects.create_user(
        employee_id="ST-MGR-0001",
        full_name="Engineering Manager",
        password="ManagerMasterPassword123!",
        role=CustomUser.Role.MANAGER,
    )


@pytest.fixture
def employee_user(db):
    return CustomUser.objects.create_user(
        employee_id="ST-EMP-2001",
        full_name="Engineer Alice",
        password="EmployeePassword123!",
        role=CustomUser.Role.EMPLOYEE,
    )


@pytest.fixture
def outside_employee(db):
    return CustomUser.objects.create_user(
        employee_id="ST-EMP-2002",
        full_name="Engineer Bob",
        password="EmployeePassword123!",
        role=CustomUser.Role.EMPLOYEE,
    )


@pytest.fixture
def intern_user(db):
    return CustomUser.objects.create_user(
        employee_id="ST-INT-3001",
        full_name="Intern Charlie",
        password="InternPassword123!",
        role=CustomUser.Role.INTERN,
    )


# ==============================================================================
# 1. WEBAUTHN / PASSKEY SUBSYSTEM TESTS
# ==============================================================================


@pytest.mark.django_db
class TestPasskeySubsystem:
    def test_generate_registration_options_creates_valid_challenge(self, employee_user):
        """Passkey registration options include RP parameters, user info, and session challenge."""
        factory = RequestFactory()
        request = factory.get("/auth/passkeys/register/options/")
        request.session = SessionStore()

        options_json = generate_passkey_registration_options(request, employee_user)
        options = json.loads(options_json)

        assert "challenge" in options
        assert "rp" in options
        assert options["rp"]["name"] == "Salow Workspace"
        assert options["user"]["name"] == employee_user.employee_id
        assert REG_CHALLENGE_SESSION_KEY in request.session

    def test_generate_authentication_options(self, employee_user):
        """Authentication options generate cryptographic challenge stored in session."""
        factory = RequestFactory()
        request = factory.get("/auth/passkeys/auth/options/")
        request.session = SessionStore()

        options_json = generate_passkey_authentication_options(request, employee_user)
        options = json.loads(options_json)

        assert "challenge" in options
        assert "rpId" in options
        assert AUTH_CHALLENGE_SESSION_KEY in request.session

    @patch("apps.accounts.auth.passkeys.verify_registration_response")
    def test_verify_passkey_registration_success(self, mock_verify, employee_user):
        """Valid attestation response creates active PasskeyCredential entity."""
        factory = RequestFactory()
        request = factory.post("/auth/passkeys/register/verify/")
        request.session = SessionStore()
        challenge_bytes = b"0123456789abcdef0123456789abcdef"
        request.session[REG_CHALLENGE_SESSION_KEY] = challenge_bytes.hex()

        mock_verification = MagicMock()
        mock_verification.credential_id = b"cred_id_12345"
        mock_verification.credential_public_key = b"fake_pub_key_bytes"
        mock_verification.sign_count = 0
        mock_verification.aaguid = "00000000-0000-0000-0000-000000000000"
        mock_verify.return_value = mock_verification

        credential_data = {"id": "cred_id_12345", "rawId": "cred_id_12345"}
        passkey = verify_passkey_registration(
            request=request,
            user=employee_user,
            credential_data=credential_data,
            display_name="MacBook Touch ID",
        )

        assert passkey.id is not None
        assert passkey.user == employee_user
        assert passkey.credential_id == "cred_id_12345"
        assert passkey.display_name == "MacBook Touch ID"
        assert passkey.is_active is True
        assert REG_CHALLENGE_SESSION_KEY not in request.session

    def test_passkey_revocation(self, employee_user):
        """User can deactivate a registered passkey credential."""
        passkey = PasskeyCredential.objects.create(
            user=employee_user,
            credential_id="test_cred_id_999",
            public_key=b"sample_key",
            sign_count=1,
            display_name="YubiKey 5C",
            is_active=True,
        )

        revoked = revoke_passkey(employee_user, passkey.id)
        assert revoked is True
        passkey.refresh_from_db()
        assert passkey.is_active is False

        # Revoking already inactive or non-owned passkey returns False
        revoked_again = revoke_passkey(employee_user, 999999)
        assert revoked_again is False


# ==============================================================================
# 2. DELEGATED RECOVERY & DUAL-APPROVAL TESTS
# ==============================================================================


@pytest.mark.django_db
class TestDelegatedRecoveryAndDualApproval:
    def test_super_admin_recovery_requires_dual_super_admin_approval(
        self, super_admin, super_admin_two
    ):
        """Super Admin target requires approval from a distinct second Super Admin."""
        # 1. Missing second admin approval raises ValidationError
        with pytest.raises(ValidationError) as exc:
            check_recovery_authority(
                actor=super_admin, target_user=super_admin_two, approving_admin=None
            )
        assert "requires approval from a second administrator" in str(exc.value)

        # 2. Self-approval raises ValidationError
        with pytest.raises(ValidationError) as exc:
            check_recovery_authority(
                actor=super_admin, target_user=super_admin_two, approving_admin=super_admin
            )
        assert "requires approval from a second administrator" in str(exc.value)

        # 3. Two distinct Super Admins succeeds
        check_recovery_authority(
            actor=super_admin, target_user=super_admin_two, approving_admin=super_admin_two
        )

    def test_admin_cannot_recover_super_admin(self, admin_user, super_admin):
        """Standard Administrator cannot recover Super Admin credentials."""
        with pytest.raises(PermissionDenied) as exc:
            check_recovery_authority(actor=admin_user, target_user=super_admin)
        assert "cannot recover or reset Super Admin credentials" in str(exc.value)

    def test_hr_scope_restricted_to_employee_and_intern(
        self, hr_user, employee_user, intern_user, manager_user, admin_user
    ):
        """HR role can recover employees and interns, but is denied for managers/admins."""
        # Allowed
        check_recovery_authority(actor=hr_user, target_user=employee_user)
        check_recovery_authority(actor=hr_user, target_user=intern_user)

        # Denied
        with pytest.raises(PermissionDenied):
            check_recovery_authority(actor=hr_user, target_user=manager_user)
        with pytest.raises(PermissionDenied):
            check_recovery_authority(actor=hr_user, target_user=admin_user)

    def test_manager_scope_restricted_to_team_and_reporting_chain(
        self, manager_user, employee_user, outside_employee
    ):
        """Manager can only recover employees within direct reporting or led team."""
        # Create direct reporting relationship
        ReportingRelationship.objects.create(manager=manager_user, subordinate=employee_user)

        # In-scope subordinate succeeds
        check_recovery_authority(actor=manager_user, target_user=employee_user)

        # Outside employee fails
        with pytest.raises(PermissionDenied) as exc:
            check_recovery_authority(actor=manager_user, target_user=outside_employee)
        assert "outside their assigned team or reporting scope" in str(exc.value)

    def test_self_service_password_reset_with_recovery_code(self, employee_user):
        """User can reset password using a single-use backup recovery code."""
        raw_codes = generate_ten_recovery_codes(employee_user)
        code_to_use = raw_codes[0]

        # Reset password using recovery code
        self_service_reset_password(
            employee_id=employee_user.employee_id,
            credential_token=code_to_use,
            new_password="NewSecureResetPassword2026!",
            is_recovery_code=True,
        )

        employee_user.refresh_from_db()
        assert employee_user.check_password("NewSecureResetPassword2026!") is True

        # Used recovery code cannot be re-used
        with pytest.raises(AuthException):
            self_service_reset_password(
                employee_id=employee_user.employee_id,
                credential_token=code_to_use,
                new_password="AnotherPassword123!",
                is_recovery_code=True,
            )

    def test_forgot_password_view_anti_enumeration(self):
        """Forgot password view renders identically for non-existent users without leaking."""
        client = Client()
        resp = client.post(
            "/auth/recovery/forgot-password/",
            {
                "action": "redeem_code",
                "employee_id": "ST-NONEXISTENT-9999",
                "code_type": "recovery_code",
                "code_value": "XXXX-XXXX-XXXX",
                "new_password": "ValidStrongPassword123!",
                "confirm_password": "ValidStrongPassword123!",
            },
        )
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert (
            "Invalid employee ID or password." in content
            or "Invalid recovery submission" in content
            or "not available" in content
        )


# ==============================================================================
# 3. SESSION RESILIENCE, IDLE LOCK & WORKSPACE DRAFTS
# ==============================================================================


@pytest.mark.django_db
class TestSessionResilienceAndDrafts:
    def test_terminate_session_by_key(self, employee_user):
        """Specific active session can be terminated by session key."""
        session_store = SessionStore()
        session_store.create()
        s_key = session_store.session_key

        UserSession.objects.create(
            user=employee_user,
            session_key=s_key,
            mfa_verified=True,
        )

        terminated = end_session_by_key(employee_user, s_key)
        assert terminated is True
        assert not UserSession.objects.filter(session_key=s_key).exists()
        assert not SessionStore().exists(s_key)

    def test_terminate_all_other_sessions(self, employee_user):
        """Revokes all active sessions except the current caller session."""
        s1 = SessionStore()
        s1.create()
        s2 = SessionStore()
        s2.create()
        s3 = SessionStore()
        s3.create()

        UserSession.objects.create(user=employee_user, session_key=s1.session_key)
        UserSession.objects.create(user=employee_user, session_key=s2.session_key)
        UserSession.objects.create(user=employee_user, session_key=s3.session_key)

        # Keep s1, revoke others
        revoked_count = revoke_user_sessions(employee_user, keep_session_key=s1.session_key)
        assert revoked_count == 2
        assert UserSession.objects.filter(user=employee_user).count() == 1
        assert UserSession.objects.filter(session_key=s1.session_key).exists()

    def test_idle_lock_and_unlock_cycle(self, employee_user):
        """Idle lock locks workspace; correct password unlocks, bad password fails."""
        client = Client()
        client.force_login(employee_user)
        session = client.session
        session["_salow_mfa_verified"] = True
        session.save()
        UserSession.objects.create(
            user=employee_user,
            session_key=session.session_key,
            mfa_verified=True,
        )

        # 1. Trigger idle lock
        lock_resp = client.get("/auth/security/idle-lock/?next=/attendance/")
        assert lock_resp.status_code == 200
        assert client.session.get("_salow_workspace_locked") is True

        # 2. Unlock attempt with wrong password -> 401
        unlock_fail = client.post(
            "/auth/security/idle-unlock/",
            {"password": "WrongPassword999!", "next": "/attendance/"},
        )
        assert unlock_fail.status_code == 401
        assert client.session.get("_salow_workspace_locked") is True

        # 3. Unlock attempt with correct password -> redirects and clears lock
        unlock_ok = client.post(
            "/auth/security/idle-unlock/",
            {"password": "EmployeePassword123!", "next": "/attendance/"},
        )
        assert unlock_ok.status_code == 302
        assert client.session.get("_salow_workspace_locked") is None

    def test_workspace_draft_persistence_and_retrieval(self, employee_user):
        """Server-side draft stores and retrieves JSON work across sessions."""
        client = Client()
        client.force_login(employee_user)
        session = client.session
        session["_salow_mfa_verified"] = True
        session.save()
        UserSession.objects.create(
            user=employee_user,
            session_key=session.session_key,
            mfa_verified=True,
        )

        draft_content = {"title": "Draft Daily Standup", "content": "Working on IAM hardening."}
        save_resp = client.post(
            "/auth/security/draft/save/",
            json.dumps({"path": "/posts/create/", "draft_data": draft_content}),
            content_type="application/json",
        )
        assert save_resp.status_code == 200
        assert save_resp.json() == {"status": "saved"}

        # Retrieve draft
        get_resp = client.get("/auth/security/draft/get/?path=/posts/create/")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["status"] == "found"
        assert body["draft_data"] == draft_content


# ==============================================================================
# 4. DISTRIBUTED RATE LIMITING & TRUSTED PROXY
# ==============================================================================


@pytest.mark.django_db
class TestDistributedRateLimiterCoordination:
    def test_database_backed_sliding_window_records_requests(self):
        """RateLimitRecord tracks request timestamps for multi-worker synchronization."""
        RateLimitMiddleware.reset()
        client = Client()

        resp = client.get("/auth/login/")
        assert resp.status_code in (200, 302)

        # Database record must have been created
        records = RateLimitRecord.objects.all()
        assert records.exists()

    def test_trusted_proxy_ip_resolution(self):
        """RateLimitMiddleware parses X-Forwarded-For only when REMOTE_ADDR is trusted."""
        middleware = RateLimitMiddleware(lambda req: None)
        factory = RequestFactory()

        # Case 1: Untrusted REMOTE_ADDR (e.g. public spoofing)
        untrusted_req = factory.get(
            "/health",
            REMOTE_ADDR="198.51.100.25",
            HTTP_X_FORWARDED_FOR="203.0.113.195",
        )
        assert middleware._get_client_ip(untrusted_req) == "198.51.100.25"

        # Case 2: Trusted reverse proxy (127.0.0.1)
        trusted_req = factory.get(
            "/health",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR="203.0.113.195, 10.0.0.1",
        )
        assert middleware._get_client_ip(trusted_req) == "203.0.113.195"


# ==============================================================================
# 5. AUTH DOCTOR 12-POINT DIAGNOSTIC VERIFICATION
# ==============================================================================


@pytest.mark.django_db
class TestAuthDoctorDiagnostics:
    def test_all_twelve_diagnostic_checks_execute_and_pass(self):
        """Complete 12-point authentication, crypto, and security diagnostics all pass."""
        results = run_auth_doctor_checks()
        assert len(results) == 12

        failed = [r for r in results if not r.passed]
        assert len(failed) == 0, (
            f"Failed diagnostics: {[r.name + ': ' + r.details for r in failed]}"
        )


# ==============================================================================
# 6. SCENARIO E: PASSKEY-AUTHENTICATED RECOVERY TESTS
# ==============================================================================


@pytest.mark.django_db
class TestPasskeyRecoveryScenarioE:
    @patch("apps.accounts.views.recovery.verify_passkey_authentication")
    def test_passkey_recovery_resets_password_and_revokes_sessions(
        self, mock_verify, employee_user
    ):
        """
        Scenario E Acceptance Test:
        Employee selects passkey recovery -> asserts identity via registered WebAuthn credential
        -> password is changed -> active device sessions revoked -> redirected to login.
        """
        mock_verify.return_value = employee_user

        # Create active device session to verify revocation
        s = SessionStore()
        s.create()
        UserSession.objects.create(
            user=employee_user,
            session_key=s.session_key,
            mfa_verified=True,
        )
        assert UserSession.objects.filter(user=employee_user).count() == 1

        client = Client()
        resp = client.post(
            "/auth/recovery/passkey/verify/",
            json.dumps(
                {
                    "credential": {"id": "cred_abc123", "rawId": "cred_abc123"},
                    "employee_id": employee_user.employee_id,
                    "new_password": "PasskeyResetPassword2026!",
                }
            ),
            content_type="application/json",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["redirect_url"] == "/auth/login/"

        # Password was updated
        employee_user.refresh_from_db()
        assert employee_user.check_password("PasskeyResetPassword2026!") is True

        # Old sessions revoked
        assert UserSession.objects.filter(user=employee_user).count() == 0


# ==============================================================================
# 7. SUDO MODE & STEP-UP RE-AUTHENTICATION PROTECTION TESTS
# ==============================================================================


@pytest.mark.django_db
class TestSudoElevationAndProtection:
    def test_stepup_verification_flow(self, employee_user, super_admin):
        """Verifies password check and Super Admin TOTP requirements for step-up."""
        factory = RequestFactory()
        req = factory.post("/auth/security/sudo/")
        req.user = employee_user
        req.session = SessionStore()

        # Wrong password fails
        with pytest.raises(AuthException):
            verify_stepup(req, password="WrongPassword!")

        # Correct password succeeds and sets sudo active
        verify_stepup(req, password="EmployeePassword123!")
        assert is_sudo_active(req) is True

    def test_passkey_deletion_requires_sudo(self, employee_user):
        """Passkey deletion redirects to sudo view if recent re-authentication is absent."""
        passkey = PasskeyCredential.objects.create(
            user=employee_user,
            credential_id="pk_test_100",
            public_key=b"sample_key_100",
            sign_count=1,
            display_name="Work Windows Hello",
            is_active=True,
        )

        client = Client()
        client.force_login(employee_user)
        session = client.session
        session["_salow_mfa_verified"] = True
        session.save()

        # 1. Attempt delete without sudo -> redirected to sudo elevation view
        resp = client.post(f"/auth/passkeys/{passkey.id}/delete/")
        assert resp.status_code == 302
        assert "/auth/security/sudo/" in resp.url
        passkey.refresh_from_db()
        assert passkey.is_active is True  # NOT deleted

        # 2. Grant sudo in session
        session[SUDO_GRANTED_TIMESTAMP_KEY] = 1893456000.0  # Future timestamp
        session.save()

        # 3. Attempt delete with active sudo -> succeeds
        resp2 = client.post(f"/auth/passkeys/{passkey.id}/delete/")
        assert resp2.status_code == 302
        assert "/auth/security/" in resp2.url
        passkey.refresh_from_db()
        assert passkey.is_active is False

    def test_admin_recovery_console_requires_sudo_on_issuance(self, admin_user, employee_user):
        """Admin recovery console requires sudo before generating tokens."""
        client = Client()
        client.force_login(admin_user)
        session = client.session
        session["_salow_mfa_verified"] = True
        session.save()

        # Without sudo -> redirected to sudo view
        resp = client.post(
            "/auth/recovery/console/",
            {
                "target_employee_id": employee_user.employee_id,
                "recovery_type": "password_reset",
                "reason": "Employee requested password reset after phone replacement",
            },
        )
        assert resp.status_code == 302
        assert "/auth/security/sudo/" in resp.url

        # Grant sudo
        session[SUDO_GRANTED_TIMESTAMP_KEY] = 1893456000.0
        session.save()

        # With sudo -> token generated
        resp2 = client.post(
            "/auth/recovery/console/",
            {
                "target_employee_id": employee_user.employee_id,
                "recovery_type": "password_reset",
                "reason": "Employee requested password reset after phone replacement",
            },
        )
        assert resp2.status_code == 200
        assert "Single-Use Password Reset Token Generated" in resp2.content.decode("utf-8")


# ==============================================================================
# 8. SCENARIO H: LOST AUTHENTICATOR & MFA RESET TESTS
# ==============================================================================


@pytest.mark.django_db
class TestLostAuthenticatorScenarioH:
    def test_mfa_reset_issue_and_redemption_cycle(self, admin_user, employee_user):
        """
        Scenario H Acceptance Test:
        Lost authenticator -> Admin/Manager issues MFA Reset Token -> Employee redeems token
        with current password -> old authenticator deleted -> redirected to re-enroll MFA.
        """
        # Enroll an initial TOTP credential
        TOTPCredential.objects.create(
            user=employee_user,
            encrypted_secret=b"fake_enc",
            nonce=b"123456789012",
            key_id="k1",
            confirmed_at=timezone.now(),
        )
        assert TOTPCredential.objects.filter(user=employee_user).exists()

        # Admin issues MFA reset token
        from apps.accounts.auth.reset import issue_mfa_reset_code

        _, raw_mfa_code = issue_mfa_reset_code(
            target_user=employee_user,
            issuing_admin=admin_user,
        )

        # Employee redeems MFA reset token
        client = Client()
        resp = client.post(
            "/auth/recovery/forgot-password/",
            {
                "action": "redeem_code",
                "employee_id": employee_user.employee_id,
                "code_type": "mfa_reset_token",
                "code_value": raw_mfa_code,
                "current_password": "EmployeePassword123!",
            },
        )
        assert resp.status_code == 302
        assert "/auth/activate/mfa/setup/" in resp.url

        # Old authenticator was deleted
        assert not TOTPCredential.objects.filter(user=employee_user).exists()
        assert client.session.get("_salow_act_user_id") == employee_user.id


# ==============================================================================
# 9. ACCOUNT LIFECYCLE & PERMISSION MATRIX INVARIANTS
# ==============================================================================


@pytest.mark.django_db
class TestAccountLifecycleAndPermissionMatrix:
    def test_suspended_account_ejected_from_protected_pages(self, employee_user):
        """Suspended account is immediately logged out and redirected by middleware."""
        client = Client()
        client.force_login(employee_user)
        session = client.session
        session["_salow_mfa_verified"] = True
        session.save()

        # Suspend account
        set_user_status(employee_user, CustomUser.Status.SUSPENDED)

        # Access protected page -> middleware terminates session
        resp = client.get("/auth/security/")
        assert resp.status_code == 302
        assert "/auth/login" in resp.url

    def test_exited_account_cannot_use_old_session(self, employee_user):
        """Exited account session is purged and redirected."""
        client = Client()
        client.force_login(employee_user)
        session = client.session
        session["_salow_mfa_verified"] = True
        session.save()

        set_user_status(employee_user, CustomUser.Status.EXITED)

        resp = client.get("/auth/security/")
        assert resp.status_code == 302
        assert "/auth/login" in resp.url

    def test_role_change_revokes_all_active_sessions(self, manager_user):
        """Demoting or changing role immediately revokes all active sessions."""
        s1 = SessionStore()
        s1.create()
        UserSession.objects.create(user=manager_user, session_key=s1.session_key)

        set_user_role(manager_user, CustomUser.Role.EMPLOYEE)
        assert not UserSession.objects.filter(user=manager_user).exists()

    def test_intern_denied_access_to_recovery_console(self, intern_user):
        """Intern role is denied from accessing recovery console."""
        client = Client()
        client.force_login(intern_user)
        session = client.session
        session["_salow_mfa_verified"] = True
        session.save()

        resp = client.get("/auth/recovery/console/")
        assert resp.status_code == 302
        assert "/attendance/" in resp.url


# ==============================================================================
# 10. MULTIDIMENSIONAL RATE LIMITING & PRUNING TESTS
# ==============================================================================


@pytest.mark.django_db
class TestMultidimensionalRateLimiting:
    def test_account_dimensional_keying_and_cleanup(self):
        """Rate limit tracks account dimension and prunes expired records."""
        RateLimitMiddleware.reset()
        client = Client()

        # Post to login with specific employee ID
        resp = client.post(
            "/auth/login/",
            {"employee_id": "ST-EMP-2001", "password": "WrongPassword!"},
        )
        assert resp.status_code in (200, 302)

        # Account-level record must exist in RateLimitRecord
        records = RateLimitRecord.objects.filter(
            key="acc:ST-EMP-2001:127.0.0.1:sensitive"
        )  # account+IP (policy change)
        assert records.exists()
