from django.contrib.auth import login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import (
    CustomUser,
    LoginHistory,
    RecoveryCode,
    UserSession,
    UserTOTPDevice,
)
from apps.accounts.totp import (
    generate_recovery_codes,
    generate_totp_secret,
    get_totp_uri,
    hash_recovery_code,
    verify_totp_token,
)
from apps.core.services import record_audit_event


def login_user(request, user) -> None:
    """
    Session-safe login handler.
    Rotates session key to prevent session fixation, tracks session, and logs audit event.
    """
    login(request, user)
    request.session.cycle_key()
    session_key = request.session.session_key
    ip_address = request.META.get("REMOTE_ADDR")
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:255]

    # Reset failed attempts
    if user.failed_login_attempts > 0:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["failed_login_attempts", "locked_until"])

    # Track active session
    if session_key:
        UserSession.objects.update_or_create(
            session_key=session_key,
            defaults={
                "user": user,
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
        )

    record_login_history(
        employee_id=user.employee_id,
        status=LoginHistory.Status.SUCCESS,
        ip_address=ip_address,
        user_agent=user_agent,
        user=user,
    )

    record_audit_event(
        event_type="accounts.login.success",
        actor=user,
        target_entity="CustomUser",
        target_id=user.employee_id,
        details={"role": user.role, "ip_address": ip_address},
    )


def logout_user(request) -> None:
    """POST-only logout handler. Invalidates server session and tracks removal."""
    user = request.user
    session_key = request.session.session_key
    if session_key:
        UserSession.objects.filter(session_key=session_key).delete()

    if user.is_authenticated:
        record_audit_event(
            event_type="accounts.logout.success",
            actor=user,
            target_entity="CustomUser",
            target_id=user.employee_id,
            details={},
        )
    logout(request)
    request.session.flush()


def record_login_history(
    employee_id: str,
    status: str,
    ip_address: str = None,
    user_agent: str = "",
    user: CustomUser = None,
) -> LoginHistory:
    """Record immutable authentication attempt."""
    return LoginHistory.objects.create(
        user=user,
        employee_id_entered=employee_id,
        ip_address=ip_address,
        user_agent=user_agent[:255],
        status=status,
    )


def change_password(
    user: CustomUser, old_password: str, new_password: str, actor: CustomUser
) -> None:
    """User-initiated password change."""
    if not user.check_password(old_password):
        raise ValidationError("Current password does not match.")

    validate_password(new_password, user)
    user.set_password(new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password", "updated_at"])

    # Revoke other sessions on password change
    UserSession.objects.filter(user=user).delete()

    record_audit_event(
        event_type="accounts.password_changed",
        actor=actor,
        target_entity="CustomUser",
        target_id=user.employee_id,
        details={},
    )


def admin_reset_password(
    admin_user: CustomUser, target_user: CustomUser, temp_password: str
) -> None:
    """Admin-controlled password reset forcing change on next login."""
    if admin_user.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can reset employee passwords.")

    validate_password(temp_password, target_user)
    target_user.set_password(temp_password)
    target_user.must_change_password = True
    target_user.failed_login_attempts = 0
    target_user.locked_until = None
    target_user.save(
        update_fields=[
            "password",
            "must_change_password",
            "failed_login_attempts",
            "locked_until",
            "updated_at",
        ]
    )

    # Invalidate all active sessions for the user
    UserSession.objects.filter(user=target_user).delete()

    record_audit_event(
        event_type="accounts.admin_password_reset",
        actor=admin_user,
        target_entity="CustomUser",
        target_id=target_user.employee_id,
        details={"must_change": True},
    )


def enroll_totp_device(user: CustomUser) -> tuple[UserTOTPDevice, str, list[str]]:
    """Initiate TOTP enrollment: returns device, provisioning URI, and recovery codes."""
    with transaction.atomic():
        secret = generate_totp_secret()
        device, _ = UserTOTPDevice.objects.update_or_create(
            user=user,
            defaults={"secret_base32": secret, "is_confirmed": False},
        )
        uri = get_totp_uri(secret, user.employee_id)

        # Generate fresh recovery codes
        RecoveryCode.objects.filter(user=user).delete()
        raw_codes = generate_recovery_codes(count=8)
        recovery_objects = [
            RecoveryCode(user=user, code_hash=hash_recovery_code(code)) for code in raw_codes
        ]
        RecoveryCode.objects.bulk_create(recovery_objects)

        return device, uri, raw_codes


def confirm_totp_device(user: CustomUser, token: str) -> bool:
    """Confirm newly enrolled TOTP device with initial valid token."""
    device = getattr(user, "totp_device", None)
    if not device:
        return False

    if verify_totp_token(device.secret_base32, token):
        device.is_confirmed = True
        device.last_verified_at = timezone.now()
        device.save(update_fields=["is_confirmed", "last_verified_at"])

        record_audit_event(
            event_type="accounts.mfa_enrolled",
            actor=user,
            target_entity="UserTOTPDevice",
            target_id=str(device.id),
            details={"employee_id": user.employee_id},
        )
        return True
    return False


def verify_user_totp(user: CustomUser, token: str) -> bool:
    """Verify active TOTP token during login or sudo elevation."""
    device = getattr(user, "totp_device", None)
    if not device or not device.is_confirmed:
        return False

    if verify_totp_token(device.secret_base32, token):
        device.last_verified_at = timezone.now()
        device.save(update_fields=["last_verified_at"])
        return True
    return False


def verify_recovery_code(user: CustomUser, code: str) -> bool:
    """Verify single-use MFA recovery code and mark as used."""
    code_hash = hash_recovery_code(code)
    recovery_entry = RecoveryCode.objects.filter(
        user=user, code_hash=code_hash, is_used=False
    ).first()
    if recovery_entry:
        recovery_entry.is_used = True
        recovery_entry.used_at = timezone.now()
        recovery_entry.save(update_fields=["is_used", "used_at"])

        record_audit_event(
            event_type="accounts.mfa_recovery_used",
            actor=user,
            target_entity="RecoveryCode",
            target_id=str(recovery_entry.id),
            details={"employee_id": user.employee_id},
        )
        return True
    return False


def transition_employee_status(
    user: CustomUser, new_status: str, reason: str, actor: CustomUser
) -> None:
    """Formal status transitions in employee lifecycle."""
    if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can transition employee lifecycle status.")

    old_status = user.status
    user.status = new_status
    if new_status in (CustomUser.Status.EXITED, CustomUser.Status.SUSPENDED):
        user.is_active = False
        UserSession.objects.filter(user=user).delete()
    elif new_status == CustomUser.Status.ACTIVE:
        user.is_active = True

    user.save(update_fields=["status", "is_active", "updated_at"])

    record_audit_event(
        event_type="accounts.lifecycle_status_transition",
        actor=actor,
        target_entity="CustomUser",
        target_id=user.employee_id,
        details={
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
        },
    )
