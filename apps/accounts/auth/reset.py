"""
Password and Multi-Factor Reset Workflows.
Implements dual-administrator approval for administrative MFA resets,
HR and Manager delegated recovery for employees and interns within scope,
and self-service recovery with one-time recovery codes and passkeys.
"""

from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.auth.activation import (
    issue_activation_code,
    verify_and_consume_code,
)
from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import verify_totp_code
from apps.accounts.auth.passwords import set_user_password, validate_employee_password
from apps.accounts.auth.recovery_codes import redeem_recovery_code
from apps.accounts.auth.sessions import revoke_user_sessions
from apps.accounts.models import ActivationCode, CustomUser, TOTPCredential


def check_recovery_authority(  # noqa: C901
    actor: CustomUser,
    target_user: CustomUser,
    approving_admin: Optional[CustomUser] = None,
) -> None:
    """
    Enforces server-side authority policy for delegated and administrative account recovery:
    - Super Admin: can recover all accounts; target Super Admin requires dual-admin approval.
    - Admin: can recover Admin, HR, Manager, Employee, Intern; cannot recover Super Admin.
             target Admin requires dual-admin approval.
    - HR: can recover Employee and Intern only.
    - Manager: can recover Employee and Intern within their direct reporting or team scope.
    - Employee/Intern: cannot perform administrative or delegated recovery.
    """
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("Authenticated active account required to initiate recovery.")

    # Guard 1: Ordinary administrators cannot reset Super Admin credentials
    if (
        target_user.role == CustomUser.Role.SUPER_ADMIN
        and actor.role != CustomUser.Role.SUPER_ADMIN
    ):
        raise PermissionDenied(
            "Ordinary administrators cannot recover or reset Super Admin credentials."
        )

    # Guard 2: Privilege level authorization
    if actor.role == CustomUser.Role.SUPER_ADMIN:
        # If target is Super Admin, enforce dual-admin rule
        if target_user.role == CustomUser.Role.SUPER_ADMIN:
            active_super_admins = CustomUser.objects.filter(
                role=CustomUser.Role.SUPER_ADMIN, is_active=True
            ).count()
            if active_super_admins < 2:
                raise ValidationError(
                    "Cannot initiate Super Admin recovery: system must maintain at least two active Super Admins."
                )
            if not approving_admin or approving_admin.id == actor.id:
                raise ValidationError(
                    "Super Admin recovery requires approval from a second administrator."
                )
            if approving_admin.role != CustomUser.Role.SUPER_ADMIN:
                raise PermissionDenied("Second approving account must be a Super Admin.")
        return

    if actor.role == CustomUser.Role.ADMIN:
        if target_user.role == CustomUser.Role.ADMIN:
            if not approving_admin or approving_admin.id == actor.id:
                raise ValidationError(
                    "Admin recovery requires approval from a second administrator."
                )
            if approving_admin.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
                raise PermissionDenied("Second approving account must be an administrator.")
        return

    if actor.role == CustomUser.Role.HR:
        if target_user.role not in (CustomUser.Role.EMPLOYEE, CustomUser.Role.INTERN):
            raise PermissionDenied("HR personnel may only recover Employee and Intern accounts.")
        return

    if actor.role == CustomUser.Role.MANAGER:
        if target_user.role not in (CustomUser.Role.EMPLOYEE, CustomUser.Role.INTERN):
            raise PermissionDenied("Managers may only recover Employee and Intern accounts.")

        # Check reporting relationship and team leadership scope
        from apps.directory.models import ReportingRelationship, Team

        is_subordinate = ReportingRelationship.objects.filter(
            manager=actor, subordinate=target_user
        ).exists()
        is_team_member = Team.objects.filter(lead=actor, memberships__user=target_user).exists()

        if not (is_subordinate or is_team_member):
            raise PermissionDenied(
                f"Manager {actor.employee_id} cannot recover account outside their assigned team or reporting scope."
            )
        return

    raise PermissionDenied("You do not have permission to recover this account.")


def issue_administrative_recovery_token(
    actor: CustomUser,
    target_user: CustomUser,
    approving_admin: Optional[CustomUser] = None,
    reason: str = "",
) -> tuple[ActivationCode, str]:
    """
    Issue a single-use PASSWORD_RESET token for target user under delegated/admin authority.
    """
    check_recovery_authority(actor, target_user, approving_admin)

    record, raw_code = issue_activation_code(
        user=target_user,
        purpose=ActivationCode.Purpose.PASSWORD_RESET,
        issued_by=actor,
    )

    approver_ref = approving_admin.employee_id if approving_admin else "N/A"
    log_auth_event(
        event="recovery_code_issued_by_authority",
        code="INFO",
        step="issue_recovery_token",
        employee_id=target_user.employee_id,
        reference_id=f"REC-{record.id}",
        message=(
            f"Recovery initiated by {actor.employee_id} ({actor.role}). "
            f"Approver: {approver_ref}. Reason: {reason}"
        ),
    )
    return record, raw_code


def complete_password_reset_with_token(
    employee_id: str,
    raw_code: str,
    new_password: str,
) -> CustomUser:
    """
    Validate PASSWORD_RESET token, set new password, and revoke all active sessions.
    """
    _, user = verify_and_consume_code(
        employee_id=employee_id,
        raw_code=raw_code,
        purpose=ActivationCode.Purpose.PASSWORD_RESET,
    )

    validate_employee_password(new_password, user)

    with transaction.atomic():
        set_user_password(user, new_password)
        revoke_user_sessions(user)

        log_auth_event(
            event="password_reset_completed",
            code="INFO",
            step="complete_password_reset",
            employee_id=user.employee_id,
            message="Password reset successfully completed; all sessions revoked",
        )

    return user


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

    validate_employee_password(new_password, user)

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
    check_recovery_authority(issuing_admin, target_user, approving_admin)

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
