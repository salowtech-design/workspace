"""
Password and Multi-Factor Reset Workflows.
Implements dual-administrator approval for administrative MFA resets.
Enforces that at least two Super Admin accounts remain active.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.auth.activation import (
    issue_activation_code,
    verify_and_consume_code,
)
from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import verify_totp_code
from apps.accounts.auth.passwords import set_user_password
from apps.accounts.auth.recovery_codes import redeem_recovery_code
from apps.accounts.auth.sessions import revoke_user_sessions
from apps.accounts.models import ActivationCode, CustomUser, TOTPCredential


def self_service_reset_password(
    employee_id: str,
    credential_token: str,
    new_password: str,
    is_recovery_code: bool = False,
) -> None:
    """
    Self-service password reset using active MFA token or recovery code.
    Revokes all sessions on completion.
    """
    user = CustomUser.objects.filter(
        employee_id=employee_id.strip().upper(), is_active=True
    ).first()
    if not user:
        raise AuthException("AUTH-LOG-001", step="forgot_password", employee_id=employee_id)

    # Prove identity via MFA
    if is_recovery_code:
        redeem_recovery_code(user, credential_token)
    else:
        verify_totp_code(user, credential_token)

    with transaction.atomic():
        set_user_password(user, new_password)
        revoke_user_sessions(user)

        log_auth_event(
            event="password_reset_self_service",
            code="INFO",
            step="forgot_password",
            employee_id=user.employee_id,
            message="Password reset successfully using MFA proof",
        )


def issue_mfa_reset_code(
    target_user: CustomUser,
    issuing_admin: CustomUser,
    approving_admin: CustomUser = None,
) -> tuple[ActivationCode, str]:
    """
    Issue an mfa_reset code.
    For Admin accounts, a second distinct administrator's approval is MANDATORY.
    """
    if issuing_admin.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can issue MFA reset codes.")

    is_target_privileged = target_user.role in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
    )

    if is_target_privileged:
        if not approving_admin:
            raise ValidationError(
                "Resetting MFA for an admin account requires approval from a second administrator."
            )
        if approving_admin.id == issuing_admin.id:
            raise ValidationError(
                "The approving administrator must be different from the issuing administrator."
            )
        if approving_admin.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
            raise PermissionDenied("Approving user must possess administrator privileges.")

    # Guard: check minimum super admin count
    if target_user.role == CustomUser.Role.SUPER_ADMIN:
        active_super_admins = CustomUser.objects.filter(
            role=CustomUser.Role.SUPER_ADMIN, is_active=True
        ).count()
        if active_super_admins < 2:
            raise ValidationError(
                "Cannot initiate MFA reset: system must maintain at least two active Super Admins."
            )

    record, raw_code = issue_activation_code(
        user=target_user,
        purpose=ActivationCode.Purpose.MFA_RESET,
        issued_by=issuing_admin,
    )

    approver_id = getattr(approving_admin, "employee_id", "N/A")
    log_auth_event(
        event="mfa_reset_code_issued",
        code="INFO",
        step="issue_mfa_reset",
        employee_id=target_user.employee_id,
        reference_id=f"RESET-{record.id}",
        message=f"Issued by {issuing_admin.employee_id} (Approved by: {approver_id})",
    )

    return record, raw_code


def verify_mfa_reset_and_authorize(employee_id: str, raw_code: str, password: str) -> CustomUser:
    """
    Validate mfa_reset code and password to authorize new authenticator enrollment.
    Deletes old TOTP credential and revokes all active sessions.
    """
    _, user = verify_and_consume_code(
        employee_id=employee_id,
        raw_code=raw_code,
        purpose=ActivationCode.Purpose.MFA_RESET,
    )

    if not user.check_password(password):
        raise AuthException("AUTH-LOG-001", step="verify_mfa_reset", employee_id=employee_id)

    with transaction.atomic():
        TOTPCredential.objects.filter(user=user).delete()
        revoke_user_sessions(user)

        log_auth_event(
            event="mfa_reset_authorized",
            code="INFO",
            step="verify_mfa_reset",
            employee_id=user.employee_id,
            message="Old MFA credentials purged; ready for re-enrollment",
        )

    return user
