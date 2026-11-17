"""
Authoritative Accounts & Identity Service Layer.
Exposes public business operations for account management, authentication,
credential updates, MFA, and session lifecycle.
"""

from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.auth.activation import (
    generate_crockford_code,
    hash_code,
    issue_activation_code,
    verify_and_consume_code,
)
from apps.accounts.auth.audit import log_auth_event
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
from apps.accounts.auth.passwords import (
    set_user_password,
    validate_employee_password,
)
from apps.accounts.auth.recovery_codes import (
    generate_ten_recovery_codes,
    redeem_recovery_code,
)
from apps.accounts.auth.reset import (
    issue_mfa_reset_code,
    self_service_reset_password,
    verify_mfa_reset_and_authorize,
)
from apps.accounts.auth.sessions import (
    clean_stale_sessions,
    create_verified_session,
    end_session_by_key,
    revoke_user_sessions,
)
from apps.accounts.models import CustomUser


def change_user_password(
    user: CustomUser,
    current_password: str,
    new_password: str,
    keep_session_key: Optional[str] = None,
) -> None:
    """
    Safely changes an authenticated user's permanent password.
    Enforces current-password verification, modern password policy validation,
    and automatic revocation of all other active device sessions.
    """
    if not user.check_password(current_password):
        log_auth_event(
            event="password_change_failed_current_mismatch",
            code="AUTH-PWD-002",
            step="change_password",
            employee_id=user.employee_id,
            message="Current password verification failed during password change",
        )
        raise ValidationError("Your current password was entered incorrectly.")

    validate_employee_password(new_password, user)

    with transaction.atomic():
        set_user_password(user, new_password)
        # Revoke other active sessions to safeguard against compromised tokens
        revoked_count = revoke_user_sessions(user, keep_session_key=keep_session_key)

        log_auth_event(
            event="password_changed",
            code="INFO",
            step="change_password",
            employee_id=user.employee_id,
            message=f"Password successfully changed; revoked {revoked_count} other sessions",
        )


def set_user_status(
    user: CustomUser,
    new_status: str,
    admin_user: Optional[CustomUser] = None,
    reason: str = "",
) -> None:
    """
    Updates user account lifecycle status.
    If transitioning to SUSPENDED, EXITED, LOCKED, or DISABLED, all active sessions
    are immediately revoked.
    """
    old_status = user.status
    user.status = new_status
    if new_status in (
        CustomUser.Status.DISABLED,
        CustomUser.Status.SUSPENDED,
        CustomUser.Status.EXITED,
    ):
        user.is_active = False
    elif new_status == CustomUser.Status.ACTIVE:
        user.is_active = True

    with transaction.atomic():
        user.save(update_fields=["status", "is_active", "updated_at"])

        # Revoke sessions if moving to an inactive/locked status
        if new_status in (
            CustomUser.Status.LOCKED,
            CustomUser.Status.DISABLED,
            CustomUser.Status.SUSPENDED,
            CustomUser.Status.EXITED,
        ):
            revoke_user_sessions(user)

        admin_ref = admin_user.employee_id if admin_user else "SYSTEM"
        log_auth_event(
            event="account_status_changed",
            code="INFO",
            step="set_user_status",
            employee_id=user.employee_id,
            message=f"Status changed from {old_status} to {new_status} by {admin_ref}. Reason: {reason}",
        )


def set_user_role(
    user: CustomUser,
    new_role: str,
    admin_user: Optional[CustomUser] = None,
    reason: str = "",
) -> None:
    """
    Updates user account role and immediately revokes all active device sessions
    so old permissions cannot remain active through a stale session.
    """
    old_role = user.role
    user.role = new_role
    with transaction.atomic():
        user.save(update_fields=["role", "updated_at"])
        revoked_count = revoke_user_sessions(user)
        admin_ref = admin_user.employee_id if admin_user else "SYSTEM"
        log_auth_event(
            event="account_role_changed",
            code="INFO",
            step="set_user_role",
            employee_id=user.employee_id,
            message=(
                f"Role changed from {old_role} to {new_role} by {admin_ref} "
                f"(revoked {revoked_count} sessions). Reason: {reason}"
            ),
        )


__all__ = [
    "authenticate_password",
    "change_user_password",
    "clean_stale_sessions",
    "complete_mfa_challenge",
    "create_verified_session",
    "decrypt_secret",
    "encrypt_secret",
    "end_session_by_key",
    "generate_crockford_code",
    "generate_ten_recovery_codes",
    "get_auth_key_ring",
    "hash_code",
    "issue_activation_code",
    "issue_mfa_reset_code",
    "redeem_recovery_code",
    "render_svg_qr",
    "revoke_user_sessions",
    "self_service_reset_password",
    "set_user_password",
    "set_user_role",
    "set_user_status",
    "validate_employee_password",
    "verify_and_consume_code",
    "verify_mfa_reset_and_authorize",
    "verify_totp_code",
]
