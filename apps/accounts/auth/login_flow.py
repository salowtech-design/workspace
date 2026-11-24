"""
Primary Authentication Orchestrator.
Orchestrates: Employee ID & Password -> Lockout Check -> MFA Challenge -> Verified Session.
"""

from typing import Optional

from django.contrib.auth.hashers import check_password, make_password

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import RETURNING_SIGNIN_LOCKOUT_THRESHOLD
from apps.accounts.auth.employee_id import validate_employee_id_role_consistency
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.lockout import (
    check_lockout,
    clear_failed_attempts,
    record_failed_attempt,
)
from apps.accounts.auth.mfa_totp import verify_totp_code
from apps.accounts.auth.recovery_codes import redeem_recovery_code
from apps.accounts.auth.sessions import create_verified_session
from apps.accounts.models import CustomUser, TOTPCredential

DUMMY_PASSWORD_HASH = make_password("SalowTimingSafeDummyPassword2026!")


def authenticate_password(
    employee_id: str,
    password: str,
    ip_address: Optional[str] = None,
) -> CustomUser:
    """
    Step 1 of everyday login: Verify employee ID and password.
    Enforces lockout, status guards, and constant-time dummy checks.
    """
    normalized_id = employee_id.strip().upper()
    lockout_key = f"emp:{normalized_id}"

    # 1. Lockout check
    check_lockout(lockout_key, employee_id=normalized_id)

    user = CustomUser.objects.filter(employee_id=normalized_id).first()

    # 2. Timing-safe password check
    if not user:
        check_password(password, DUMMY_PASSWORD_HASH)
        record_failed_attempt(
            lockout_key, employee_id=normalized_id, threshold=RETURNING_SIGNIN_LOCKOUT_THRESHOLD
        )
        log_auth_event(
            event="login_failed_user_not_found",
            code="AUTH-LOG-001",
            step="auth_password",
            employee_id=normalized_id,
            network_address=ip_address,
            message="Invalid credentials entered",
        )
        raise AuthException("AUTH-LOG-001", step="auth_password", employee_id=normalized_id)

    # 3. Employee ID role marker consistency check (Section 6)
    is_role_ok, role_err = validate_employee_id_role_consistency(user.employee_id, user.role)
    if not is_role_ok:
        record_failed_attempt(
            lockout_key, employee_id=normalized_id, threshold=RETURNING_SIGNIN_LOCKOUT_THRESHOLD
        )
        log_auth_event(
            event="role_marker_mismatch",
            code="AUTH-SEC-001",
            step="auth_password",
            employee_id=normalized_id,
            network_address=ip_address,
            message=role_err or "Role marker mismatch against assigned role",
        )
        # Anti-enumeration: preserve generic failure message
        raise AuthException("AUTH-LOG-001", step="auth_password", employee_id=normalized_id)

    # 4. Account status guards
    if user.status in (CustomUser.Status.INVITED, CustomUser.Status.ACTIVATING):
        log_auth_event(
            event="login_rejected_unactivated",
            code="AUTH-LOG-003",
            step="auth_password",
            employee_id=normalized_id,
            network_address=ip_address,
            message=f"Attempt to login to {user.status} account via everyday login",
        )
        raise AuthException(
            "AUTH-LOG-003",
            step="auth_password",
            employee_id=normalized_id,
            message_override=(
                "This account has not been activated. Please complete first-time activation."
            ),
        )

    if not user.is_active or user.status == CustomUser.Status.DISABLED:
        log_auth_event(
            event="login_rejected_disabled",
            code="AUTH-LOG-003",
            step="auth_password",
            employee_id=normalized_id,
            network_address=ip_address,
            message="Attempt to login to deactivated account",
        )
        raise AuthException("AUTH-LOG-003", step="auth_password", employee_id=normalized_id)

    # 5. Check password
    if not user.check_password(password):
        record_failed_attempt(
            lockout_key, employee_id=normalized_id, threshold=RETURNING_SIGNIN_LOCKOUT_THRESHOLD
        )
        log_auth_event(
            event="login_failed_wrong_password",
            code="AUTH-LOG-001",
            step="auth_password",
            employee_id=normalized_id,
            network_address=ip_address,
            message="Invalid credentials entered",
        )
        raise AuthException("AUTH-LOG-001", step="auth_password", employee_id=normalized_id)

    log_auth_event(
        event="password_verified",
        code="INFO",
        step="auth_password",
        employee_id=normalized_id,
        network_address=ip_address,
        message="Password authenticated; awaiting MFA challenge",
    )
    return user


def complete_mfa_challenge(
    request,
    user: CustomUser,
    code_or_recovery: str,
    is_recovery_code: bool = False,
) -> None:
    """
    Step 2 of everyday login: Verify 6-digit TOTP or single-use recovery code.
    Establishes verified session and clears failed attempt counters.
    """
    normalized_input = code_or_recovery.strip()
    lockout_key = f"emp:{user.employee_id}"

    # Check if user has confirmed TOTP
    cred = TOTPCredential.objects.filter(user=user, confirmed_at__isnull=False).first()
    if not cred:
        # User is a bootstrap super admin without MFA -> must enroll first
        raise AuthException("AUTH-MFA-006", step="mfa_challenge", employee_id=user.employee_id)

    if is_recovery_code:
        redeem_recovery_code(user, normalized_input)
    else:
        verify_totp_code(user, normalized_input)

    # SUCCESS: Establish verified session and clear lockout
    create_verified_session(request, user)
    clear_failed_attempts(lockout_key)
