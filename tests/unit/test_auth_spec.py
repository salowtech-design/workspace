"""
Unit & Integration Tests for Authentication Specification (v1.0).
Tests all positive and negative security invariants, activation, TOTP MFA,
recovery codes, password policies, lockouts, and session management.
"""

from datetime import timedelta

import pyotp
import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.auth.activation import (
    generate_crockford_code,
    hash_code,
    issue_activation_code,
    verify_and_consume_code,
)
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.lockout import (
    check_lockout,
    clear_failed_attempts,
    record_failed_attempt,
)
from apps.accounts.auth.login_flow import (
    authenticate_password,
    complete_mfa_challenge,
)
from apps.accounts.auth.mfa_totp import (
    decrypt_secret,
    encrypt_secret,
    get_auth_key_ring,
    render_svg_qr,
    verify_totp_code,
)
from apps.accounts.auth.passwords import validate_employee_password
from apps.accounts.auth.recovery_codes import (
    generate_ten_recovery_codes,
    redeem_recovery_code,
)
from apps.accounts.models import (
    CustomUser,
    TOTPCredential,
)

User = get_user_model()


@pytest.fixture
def test_user(db):
    return CustomUser.objects.create_user(
        employee_id="ST-EMP-1001",
        full_name="Alice Specialist",
        password="ValidStrongPassword123!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.INVITED,
    )


@pytest.fixture
def admin_user(db):
    return CustomUser.objects.create_superuser(
        employee_id="ST-ADM-0001",
        full_name="System Admin",
        password="SuperAdminMasterPassword2026!",
    )


@pytest.mark.django_db
class TestActivationEngine:
    def test_crockford_code_generation_and_normalization(self):
        """Codes must format as XXXXX-XXXXX and Crockford alphabet excludes I, L, O, U."""
        code = generate_crockford_code()
        assert len(code) == 11
        assert code[5] == "-"
        for c in code.replace("-", ""):
            assert c not in "ILOUilou"

        # Normalization produces identical HMAC regardless of casing/spacing
        h1 = hash_code(code)
        h2 = hash_code(code.lower().replace("-", " "))
        assert h1 == h2

    def test_issue_activation_code_and_single_live_constraint(self, test_user, admin_user):
        """Only one active activation code can be live per user and purpose."""
        code_obj1, raw1 = issue_activation_code(test_user, issued_by=admin_user)
        assert code_obj1.is_live == 1
        assert code_obj1.attempts == 0

        # Issuing a second code invalidates the first
        code_obj2, raw2 = issue_activation_code(test_user, issued_by=admin_user)
        code_obj1.refresh_from_db()
        assert code_obj1.is_live is None
        assert code_obj2.is_live == 1
        assert raw1 != raw2

    def test_consume_activation_code_success(self, test_user, admin_user):
        """Valid code consumption updates status and consumes the code."""
        code_obj, raw_code = issue_activation_code(test_user, issued_by=admin_user)
        consumed_record, user = verify_and_consume_code(test_user.employee_id, raw_code)
        assert user.id == test_user.id
        assert user.status == CustomUser.Status.ACTIVATING

        code_obj.refresh_from_db()
        assert code_obj.consumed_at is not None
        assert code_obj.is_live is None

    def test_consume_activation_code_replay_rejected(self, test_user, admin_user):
        """Consumed activation code cannot be reused."""
        _, raw_code = issue_activation_code(test_user, issued_by=admin_user)
        verify_and_consume_code(test_user.employee_id, raw_code)

        with pytest.raises(AuthException) as exc_info:
            verify_and_consume_code(test_user.employee_id, raw_code)
        assert exc_info.value.code == "AUTH-ACT-001"

    def test_activation_code_attempt_limit_locks(self, test_user, admin_user):
        """5 failed attempts on an activation code burns it."""
        code_obj, raw_code = issue_activation_code(test_user, issued_by=admin_user)
        wrong_code = "99999-99999"

        for _ in range(4):
            with pytest.raises(AuthException) as exc_info:
                verify_and_consume_code(test_user.employee_id, wrong_code)
            assert exc_info.value.code == "AUTH-ACT-001"

        code_obj.refresh_from_db()
        assert code_obj.attempts == 4
        assert code_obj.is_live == 1

        # 5th attempt locks it
        with pytest.raises(AuthException) as exc_info:
            verify_and_consume_code(test_user.employee_id, wrong_code)
        assert exc_info.value.code == "AUTH-ACT-002"

        code_obj.refresh_from_db()
        assert code_obj.attempts >= 5
        assert code_obj.is_live is None

    def test_expired_activation_code_rejected(self, test_user, admin_user):
        """Codes expired beyond 48 hours are rejected."""
        code_obj, raw_code = issue_activation_code(test_user, issued_by=admin_user)
        code_obj.expires_at = timezone.now() - timedelta(hours=1)
        code_obj.save()

        with pytest.raises(AuthException) as exc_info:
            verify_and_consume_code(test_user.employee_id, raw_code)
        assert exc_info.value.code == "AUTH-ACT-001"


@pytest.mark.django_db
class TestPasswordPolicy:
    def test_password_length_constraint(self, test_user):
        """Passwords under 12 characters are rejected."""
        with pytest.raises(AuthException) as exc_info:
            validate_employee_password("Short1!", test_user)
        assert exc_info.value.code == "AUTH-PWD-001"

    def test_password_cannot_contain_employee_id_or_name(self, test_user):
        """Password cannot contain username/employee ID or parts of full name."""
        with pytest.raises(AuthException) as exc_info:
            validate_employee_password(f"SecretPass_{test_user.employee_id}_2026!", test_user)
        assert exc_info.value.code == "AUTH-PWD-001"

        with pytest.raises(AuthException) as exc_info:
            validate_employee_password("AliceInWonderlandPassword2026!", test_user)
        assert exc_info.value.code == "AUTH-PWD-001"

    def test_password_common_dictionary_rejected(self, test_user):
        """Common passwords are rejected."""
        with pytest.raises(AuthException) as exc_info:
            validate_employee_password("password1234", test_user)
        assert exc_info.value.code == "AUTH-PWD-001"

    def test_valid_strong_password_accepted(self, test_user):
        """Valid complex password passes policy."""
        valid_pass = "XyloPhone#99BicycleTire"
        validate_employee_password(valid_pass, test_user)


@pytest.mark.django_db
class TestTOTPMFAEngine:
    def test_totp_secret_encryption_at_rest(self, test_user):
        """TOTP secret is AES-256-GCM encrypted; plaintext is never stored."""
        kr = get_auth_key_ring()
        secret = pyotp.random_base32(length=32)
        ciphertext, nonce, key_id = encrypt_secret(secret, kr)

        cred = TOTPCredential.objects.create(
            user=test_user,
            encrypted_secret=ciphertext,
            nonce=nonce,
            key_id=key_id,
        )
        assert cred.encrypted_secret != secret.encode()
        decrypted = decrypt_secret(cred.encrypted_secret, cred.nonce, cred.key_id, kr)
        assert decrypted == secret

    def test_totp_verification_and_anti_replay(self, test_user):
        """TOTP code verifies correctly and anti-replay prevents reusing code in same time step."""
        kr = get_auth_key_ring()
        secret = pyotp.random_base32()
        ciphertext, nonce, key_id = encrypt_secret(secret, kr)
        cred = TOTPCredential.objects.create(
            user=test_user,
            encrypted_secret=ciphertext,
            nonce=nonce,
            key_id=key_id,
            confirmed_at=timezone.now(),
        )

        totp = pyotp.TOTP(secret)
        current_code = totp.now()

        # Wrong code fails
        with pytest.raises(AuthException) as exc_info:
            verify_totp_code(test_user, "000000")
        assert exc_info.value.code == "AUTH-MFA-002"

        # Correct code succeeds
        success = verify_totp_code(test_user, current_code)
        assert success is True
        cred.refresh_from_db()
        assert cred.last_used_step > 0

        # Anti-replay: second verification with same code in same step must fail
        with pytest.raises(AuthException) as exc_info:
            verify_totp_code(test_user, current_code)
        assert exc_info.value.code == "AUTH-MFA-003"

    def test_render_svg_qr_code(self):
        """QR code renders as standalone pure Python SVG without remote network calls."""
        uri = "otpauth://totp/Salowtech:EMP1001?secret=JBSWY3DPEHPK3PXP"
        svg = render_svg_qr(uri)
        assert "<svg" in svg
        assert "</svg>" in svg
        assert "<script" not in svg


@pytest.mark.django_db
class TestRecoveryCodes:
    def test_recovery_codes_generation_and_consumption(self, test_user):
        """Generates 10 single-use recovery codes; each can only be consumed once."""
        raw_codes = generate_ten_recovery_codes(test_user)
        assert len(raw_codes) == 10
        assert test_user.recovery_codes.count() == 10

        # Consume first code
        code_to_use = raw_codes[0]
        success = redeem_recovery_code(test_user, code_to_use)
        assert success is True

        # Re-use must fail
        with pytest.raises(AuthException):
            redeem_recovery_code(test_user, code_to_use)

        # Invalid code must fail
        with pytest.raises(AuthException):
            redeem_recovery_code(test_user, "XXXX-XXXX-XXXX")


@pytest.mark.django_db
class TestLockoutAndLoginFlow:
    def test_lockout_counter_increments_and_resets(self):
        """Failed attempts increment counter; lock triggers after max threshold (5)."""
        key = "login:test_ip_01"
        clear_failed_attempts(key)

        # Initially not locked
        check_lockout(key)

        for _ in range(4):
            record_failed_attempt(key)
            check_lockout(key)

        # 5th attempt locks out
        record_failed_attempt(key)
        with pytest.raises(AuthException) as exc_info:
            check_lockout(key)
        assert exc_info.value.code == "AUTH-LOG-002"

        # Clear clears lockout
        clear_failed_attempts(key)
        check_lockout(key)

    def test_login_flow_requires_mfa_before_active(self, test_user):
        """Primary authentication succeeds, then complete_mfa_challenge verifies session."""
        test_user.status = CustomUser.Status.ACTIVE
        test_user.save()

        # Enroll TOTP for test_user
        kr = get_auth_key_ring()
        secret = pyotp.random_base32()
        ciphertext, nonce, key_id = encrypt_secret(secret, kr)
        TOTPCredential.objects.create(
            user=test_user,
            encrypted_secret=ciphertext,
            nonce=nonce,
            key_id=key_id,
            confirmed_at=timezone.now(),
        )

        # Authenticate password
        authenticated_user = authenticate_password(
            employee_id=test_user.employee_id,
            password="ValidStrongPassword123!",
        )
        assert authenticated_user.id == test_user.id

        # Setup request session for MFA challenge
        factory = RequestFactory()
        request = factory.post("/auth/mfa/challenge/")
        from django.contrib.sessions.middleware import SessionMiddleware

        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)

        # Generate TOTP code
        totp = pyotp.TOTP(secret)
        code = totp.now()

        # Complete MFA challenge
        complete_mfa_challenge(request, authenticated_user, code)
        assert request.session.get("_salow_mfa_verified") is True

    def test_employee_id_syntax_and_role_marker_parsing(self):
        """Validates format normalization and role marker parsing per Section 6."""
        from apps.accounts.auth.employee_id import (
            normalize_employee_id,
            parse_role_marker_from_id,
            validate_employee_id_role_consistency,
        )

        assert normalize_employee_id("  st-emp-0001  ") == "ST-EMP-0001"
        assert parse_role_marker_from_id("ST-EMP-0001") == "EMP"
        assert parse_role_marker_from_id("ALICE-DEV") == "DEV"
        assert parse_role_marker_from_id("JOHN-ADMIN-01") == "ADMIN"

        # Valid role consistency
        is_ok, _ = validate_employee_id_role_consistency("ST-EMP-0001", CustomUser.Role.EMPLOYEE)
        assert is_ok is True

        # Role mismatch: identifier claims ADMIN but account is EMPLOYEE
        is_bad, reason = validate_employee_id_role_consistency("ALICE-ADMIN-01", CustomUser.Role.EMPLOYEE)
        assert is_bad is False
        assert "does not match assigned role" in reason

    def test_employee_id_role_marker_mismatch_rejected_in_login(self, db):
        """User with mismatched ID role marker (e.g. claims ADMIN in ID) is rejected per Section 6."""
        user = CustomUser.objects.create(
            employee_id="SPOOF-ADMIN-01",
            full_name="Privilege Impersonator",
            role=CustomUser.Role.EMPLOYEE,  # Authoritative DB role is EMPLOYEE, not ADMIN!
            status=CustomUser.Status.ACTIVE,
        )
        user.set_password("ValidStrongPassword123!")
        user.save()

        with pytest.raises(AuthException) as exc_info:
            authenticate_password(employee_id="SPOOF-ADMIN-01", password="ValidStrongPassword123!")
        # Preserves anti-enumeration generic code
        assert exc_info.value.code == "AUTH-LOG-001"

    def test_returning_employee_sign_in_locks_after_three_failed_attempts(self, db):
        """Returning employee sign-in enforces 3-attempt lockout per Section 7 table."""
        user = CustomUser.objects.create(
            employee_id="ST-EMP-THR-01",
            full_name="Three Attempt User",
            role=CustomUser.Role.EMPLOYEE,
            status=CustomUser.Status.ACTIVE,
        )
        user.set_password("ValidStrongPassword123!")
        user.save()

        # Attempt 1: wrong password
        with pytest.raises(AuthException):
            authenticate_password("ST-EMP-THR-01", "WrongPassword1!")

        # Attempt 2: wrong password
        with pytest.raises(AuthException):
            authenticate_password("ST-EMP-THR-01", "WrongPassword2!")

        # Attempt 3: wrong password -> engages lockout
        with pytest.raises(AuthException):
            authenticate_password("ST-EMP-THR-01", "WrongPassword3!")

        # 4th attempt: blocked by lockout even with correct password
        with pytest.raises(AuthException) as exc:
            authenticate_password("ST-EMP-THR-01", "ValidStrongPassword123!")
        assert exc.value.code == "AUTH-LOG-002"
        assert "consecutive failures" in str(exc.value)
