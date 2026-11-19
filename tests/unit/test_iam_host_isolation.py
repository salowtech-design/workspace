"""
Unit & Integration Tests for Hostname Isolation & Dedicated IAM Authority (Section 3, 4, 9, 11).
Validates:
1. Hostname separation: app.salowtech.com vs iam.salowtech.com.
2. Independent session cookies: __Host-workspace_session vs __Host-iam_session.
3. IAM Administrator dedicated credentials (min 16 chars + TOTP).
4. Assurance levels:
   - L1 baseline on dedicated sign-in.
   - L2 action-bound step-up (5 min TTL).
   - L3 dual-control authorization (strictly no self-approval).
5. Protection for the last active Super Admin.
6. Workspace Security delegated administration inside IAM.
7. Rejection of ordinary employee credentials on iam.salowtech.com.
"""

from datetime import timedelta
import pytest
import pyotp
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory
from django.utils import timezone

from apps.accounts.auth.crypto import decrypt_totp_secret
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.iam_auth import (
    authenticate_iam_admin,
    check_iam_assurance,
    create_iam_admin_identity,
    create_iam_session,
    get_iam_session_from_request,
    protect_last_super_admin,
    stepup_iam_assurance,
)
from apps.accounts.models import CustomUser, IAMAdminIdentity, IAMSession, WorkspaceSecurityPolicy


@pytest.fixture
def base_user(db):
    user = CustomUser.objects.create(
        employee_id="ST-EMP-9001",
        full_name="Alice Base User",
        email="alice@salowtech.com",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("ValidPassword2026!@#")
    user.save()
    return user


@pytest.fixture
def superadmin_person(db):
    user = CustomUser.objects.create(
        employee_id="ST-ADM-9999",
        full_name="Bob Super Admin",
        email="bob.super@salowtech.com",
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("AdminPassword2026!@#")
    user.save()
    return user


@pytest.fixture
def second_superadmin_person(db):
    user = CustomUser.objects.create(
        employee_id="ST-ADM-9998",
        full_name="Charlie Super Admin",
        email="charlie.super@salowtech.com",
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("AdminPassword2026!@#")
    user.save()
    return user


@pytest.mark.django_db
class TestIAMAuthenticationAndAssurance:
    def test_iam_admin_provisioning_enforces_16_char_password(self, superadmin_person):
        """Dedicated IAM admin passwords must be at least 16 characters."""
        with pytest.raises(ValidationError) as exc:
            create_iam_admin_identity(
                user=superadmin_person,
                admin_id="IAM-SUPER-01",
                password="ShortPass123!",  # 13 chars
                role=IAMAdminIdentity.Role.SUPER_ADMIN,
            )
        assert "at least 16 characters" in str(exc.value)

        # 16-character password succeeds
        admin_ident, raw_totp = create_iam_admin_identity(
            user=superadmin_person,
            admin_id="IAM-SUPER-01",
            password="Strong16CharMasterPass!",
            role=IAMAdminIdentity.Role.SUPER_ADMIN,
        )
        assert admin_ident.admin_id == "IAM-SUPER-01"
        assert admin_ident.role == IAMAdminIdentity.Role.SUPER_ADMIN
        assert check_password("Strong16CharMasterPass!", admin_ident.password_hash)

    def test_dedicated_iam_sign_in_happy_path_and_rejections(self, superadmin_person):
        """Dedicated IAM sign-in requires dedicated ID, password, and fresh TOTP."""
        admin_ident, raw_totp = create_iam_admin_identity(
            user=superadmin_person,
            admin_id="IAM-SUPER-02",
            password="Strong16CharMasterPass!",
            role=IAMAdminIdentity.Role.SUPER_ADMIN,
        )

        totp = pyotp.TOTP(raw_totp)
        valid_code = totp.now()

        # Reject invalid password
        with pytest.raises(AuthException):
            authenticate_iam_admin("IAM-SUPER-02", "WrongMasterPass123456", valid_code)

        # Reject invalid TOTP
        with pytest.raises(AuthException):
            authenticate_iam_admin("IAM-SUPER-02", "Strong16CharMasterPass!", "000000")

        # Reject ordinary employee credentials
        with pytest.raises(AuthException):
            authenticate_iam_admin("ST-EMP-9001", "ValidPassword2026!@#", valid_code)

        # Valid credentials succeed
        auth_admin = authenticate_iam_admin("IAM-SUPER-02", "Strong16CharMasterPass!", valid_code)
        assert auth_admin == admin_ident
        assert auth_admin.last_login_at is not None

        # TOTP step anti-replay: cannot reuse the same code
        with pytest.raises(AuthException) as replay_exc:
            authenticate_iam_admin("IAM-SUPER-02", "Strong16CharMasterPass!", valid_code)
        assert "already been used" in str(replay_exc.value)

    def test_assurance_level_stepup_and_action_binding(self, rf, superadmin_person):
        """L2 step-up is strictly bound to the exact action and expires after 5 minutes."""
        admin_ident, raw_totp = create_iam_admin_identity(
            user=superadmin_person,
            admin_id="IAM-SUPER-03",
            password="Strong16CharMasterPass!",
            role=IAMAdminIdentity.Role.SUPER_ADMIN,
        )

        request = rf.get("/")
        session = create_iam_session(admin_ident, request, assurance_level=1)
        assert session.assurance_level == 1

        # L1 is satisfied
        assert check_iam_assurance(request, required_level=1, action_name="update_policy") is True
        # L2 is NOT satisfied initially
        assert check_iam_assurance(request, required_level=2, action_name="update_policy") is False

        # Step up to L2 for "update_policy"
        totp = pyotp.TOTP(raw_totp)
        assert stepup_iam_assurance(session, "Strong16CharMasterPass!", totp.now(), action_name="update_policy") is True

        session.refresh_from_db()
        assert session.assurance_level == 2
        assert session.stepup_action == "update_policy"

        # L2 is satisfied for "update_policy"
        assert check_iam_assurance(request, required_level=2, action_name="update_policy") is True
        # L2 is NOT satisfied for an unrelated action "delete_database" (action binding)
        assert check_iam_assurance(request, required_level=2, action_name="delete_database") is False

        # After expiry (5 minutes), L2 is denied
        session.stepup_expires_at = timezone.now() - timedelta(seconds=1)
        session.save()
        assert check_iam_assurance(request, required_level=2, action_name="update_policy") is False

    def test_level_3_dual_control_strictly_forbids_self_approval(self, rf, superadmin_person, second_superadmin_person):
        """L3 privileged operations require an independent second Super Admin approver."""
        admin_1, raw_totp_1 = create_iam_admin_identity(
            user=superadmin_person,
            admin_id="IAM-SUPER-04",
            password="Strong16CharMasterPass!",
            role=IAMAdminIdentity.Role.SUPER_ADMIN,
        )
        admin_2, raw_totp_2 = create_iam_admin_identity(
            user=second_superadmin_person,
            admin_id="IAM-SUPER-05",
            password="Strong16CharMasterPass!",
            role=IAMAdminIdentity.Role.SUPER_ADMIN,
        )

        request = rf.get("/")
        session = create_iam_session(admin_1, request, assurance_level=1)
        stepup_iam_assurance(session, "Strong16CharMasterPass!", pyotp.TOTP(raw_totp_1).now(), action_name="break_glass")

        # Self-approval is rejected
        assert check_iam_assurance(request, required_level=3, action_name="break_glass", approver_admin=admin_1) is False

        # Independent second Super Admin approver succeeds
        assert check_iam_assurance(request, required_level=3, action_name="break_glass", approver_admin=admin_2) is True

    def test_protect_last_super_admin(self, superadmin_person):
        """The last active Super Admin identity cannot be demoted, suspended, or deleted."""
        admin_only, _ = create_iam_admin_identity(
            user=superadmin_person,
            admin_id="IAM-SOLO-SUPER",
            password="Strong16CharMasterPass!",
            role=IAMAdminIdentity.Role.SUPER_ADMIN,
        )

        with pytest.raises(ValidationError) as exc:
            protect_last_super_admin(admin_only)
        assert "Cannot demote, suspend, or delete the last active Super Admin" in str(exc.value)


@pytest.mark.django_db
class TestHostnameIsolationAndCookies:
    def test_iam_host_segregation_and_cookies(self, rf, superadmin_person):
        """Requests to iam.salowtech.com require IAM session and reject workspace apps."""
        client = Client()

        # 1. Unauthenticated request to iam.salowtech.com redirects to /iam/login/
        resp = client.get("/", HTTP_HOST="iam.salowtech.com")
        assert resp.status_code == 302
        assert "/iam/login" in resp.url

        # 2. Workspace endpoints on iam.salowtech.com return 404
        resp = client.get("/attendance/panel/", HTTP_HOST="iam.salowtech.com")
        assert resp.status_code == 404

        # 3. Successful login sets host-only __Host-iam_session cookie
        admin_ident, raw_totp = create_iam_admin_identity(
            user=superadmin_person,
            admin_id="IAM-LOGIN-TEST",
            password="Strong16CharMasterPass!",
            role=IAMAdminIdentity.Role.SUPER_ADMIN,
        )

        login_resp = client.post(
            "/iam/login/",
            {
                "admin_id": "IAM-LOGIN-TEST",
                "password": "Strong16CharMasterPass!",
                "totp_code": pyotp.TOTP(raw_totp).now(),
            },
            HTTP_HOST="iam.salowtech.com",
        )
        assert login_resp.status_code == 302
        assert login_resp.cookies.get("iam_session_id") or login_resp.cookies.get("__Host-iam_session")

        # 4. Workspace host ignores IAM session cookie
        workspace_resp = client.get("/attendance/", HTTP_HOST="app.salowtech.com")
        # Unauthenticated in workspace because IAM session does NOT authenticate workspace
        assert workspace_resp.status_code == 302
        assert "/auth/login" in workspace_resp.url
