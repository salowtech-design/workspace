"""
Password and Multi-Factor Reset Workflows.
Implements dual-administrator approval for administrative MFA resets,
HR and Manager delegated recovery for employees and interns within scope,
and self-service recovery with one-time recovery codes and passkeys.
"""

from datetime import timedelta
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.activation import (
    consume_email_token,
    issue_activation_code,
    issue_email_token,
    verify_and_consume_code,
    verify_email_token_safe,
)
from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import is_allowed_company_email
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import verify_totp_code
from apps.accounts.auth.passwords import set_user_password, validate_employee_password
from apps.accounts.auth.recovery_codes import redeem_recovery_code
from apps.accounts.auth.sessions import revoke_user_sessions
from apps.accounts.models import ActivationCode, CustomUser, RecoveryRequest, TOTPCredential
from apps.core.email_service import (
    notify_admin_reset_approved,
    notify_admin_reset_rejected,
    notify_admin_reset_requested,
    notify_email_reset_link_requested,
    notify_mfa_replacement_requested,
    notify_mfa_replacement_success,
    notify_password_changed,
)


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
        elif target_user.role == CustomUser.Role.ADMIN:
            # Same dual-control rule as for an Admin actor: no single administrator, whatever
            # their role, may recover another Admin account alone.
            if not approving_admin or approving_admin.id == actor.id:
                raise ValidationError(
                    "Admin recovery requires approval from a second administrator."
                )
            if approving_admin.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
                raise PermissionDenied("Second approving account must be an administrator.")
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


# ---------------------------------------------------------------------------
# Method 1 — Administrator Password Reset Request & Approvals
# ---------------------------------------------------------------------------


def request_administrator_reset(employee_id: str, reason: str = "") -> None:
    """
    Method 1: Request Administrator Reset.
    Safe generic submission: does not reveal whether the account exists.
    If the account exists and is eligible, creates a pending RecoveryRequest,
    creates administrator notification event, and queues email notification.
    """
    if not employee_id:
        return

    normalized_id = employee_id.strip().upper()
    user = CustomUser.objects.filter(employee_id=normalized_id, is_active=True).first()

    if user and user.status in (CustomUser.Status.ACTIVE, CustomUser.Status.LOCKED):
        with transaction.atomic():
            # Check for existing pending request to avoid duplicates
            existing = RecoveryRequest.objects.filter(
                user=user,
                request_type=RecoveryRequest.RequestType.ADMIN_PASSWORD_RESET,
                status=RecoveryRequest.Status.PENDING,
            ).first()

            if not existing:
                req = RecoveryRequest.objects.create(
                    user=user,
                    request_type=RecoveryRequest.RequestType.ADMIN_PASSWORD_RESET,
                    status=RecoveryRequest.Status.PENDING,
                    reason=reason or "Employee requested administrator reset.",
                )

                log_auth_event(
                    event="admin_recovery_requested",
                    code="INFO",
                    step="request_admin_reset",
                    employee_id=user.employee_id,
                    reference_id=f"REQ-{req.id}",
                    message="Administrator recovery request submitted and queued",
                )

                # Queue notification to administrators (send to IT/Admin inbox or active Super Admins)
                admin_users = CustomUser.objects.filter(
                    role__in=[CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN],
                    is_active=True,
                    email__isnull=False,
                )
                admin_emails = [admin.email for admin in admin_users if admin.email]
                target_admin_email = admin_emails[0] if admin_emails else "admin@salowtech.com"

                notify_admin_reset_requested(
                    employee_id=user.employee_id,
                    admin_email=target_admin_email,
                    employee_email=user.email if (user.email and user.email_verified) else None,
                )


def approve_administrator_reset(
    request_id: int,
    actor: CustomUser,
    second_approver: Optional[CustomUser] = None,
    base_url: str = "",
    assurance_verified: bool = False,
) -> tuple[RecoveryRequest, str]:
    """
    Approves an administrator password reset request.
    Enforces sudo verification, dual-approval policy for Super Admin, and distinct approver.
    Generates a single-use password_reset token and queues notification to verified email.
    """
    from apps.accounts.auth.stepup import is_sudo_active

    # assurance_verified is set only by the dual-control path, which has already
    # validated both approvers' own fresh Level 2 step-up against the same request.
    if not assurance_verified and not is_sudo_active(actor):
        raise PermissionDenied("Recent sudo re-authentication required for recovery approval.")

    with transaction.atomic():
        req = (
            RecoveryRequest.objects.select_for_update()
            .filter(id=request_id, status=RecoveryRequest.Status.PENDING)
            .select_related("user")
            .first()
        )
        if not req:
            raise ValidationError("Recovery request not found or not in pending state.")

        target_user = req.user

        # Prohibit self-approval
        if actor.id == target_user.id:
            raise PermissionDenied("Administrators cannot approve their own recovery request.")

        # Check recovery authority
        check_recovery_authority(actor, target_user, second_approver)

        # Generate single-use token (keyed hash stored in ActivationCode)
        code_record, raw_token = issue_email_token(
            user=target_user,
            purpose=ActivationCode.Purpose.PASSWORD_RESET,
            ttl_minutes=15,
        )

        req.status = RecoveryRequest.Status.APPROVED
        req.approved_by = actor
        req.approved_at = timezone.now()
        req.token_hash = code_record.keyed_hash
        req.reset_token = ""  # Plaintext token is never persisted in storage
        req.token_expires_at = timezone.now() + timezone.timedelta(minutes=15)

        if second_approver:
            req.second_approved_by = second_approver
            req.second_approved_at = timezone.now()

        req.save()

        reset_url = f"{base_url.rstrip('/')}/auth/recovery/email/reset/?token={raw_token}"
        if target_user.email and target_user.email_verified:
            notify_admin_reset_approved(
                employee_id=target_user.employee_id,
                employee_email=target_user.email,
                reset_url=reset_url,
            )

        log_auth_event(
            event="admin_recovery_approved",
            code="INFO",
            step="approve_admin_reset",
            employee_id=target_user.employee_id,
            reference_id=f"REQ-{req.id}",
            message=f"Recovery request approved by {actor.employee_id}",
        )

    return req, raw_token


DUAL_RECOVERY_ACTION = "approve_recovery"
# Lifetime of one IAM Level 2 step-up (matches stepup_iam_assurance); the first
# approver's Level 2 must still be inside it when the second approver completes.
IAM_LEVEL2_TTL = timedelta(minutes=5)
_PRIVILEGED_RECOVERY_ROLES = (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN)


def recovery_needs_dual_control(target_user: CustomUser) -> bool:
    """Privileged accounts (Super Admin, Admin) need two different approvers."""
    return target_user.role in _PRIVILEGED_RECOVERY_ROLES


def _check_privileged_recovery_role(actor: CustomUser, target_user: CustomUser) -> None:
    if not actor.is_active or actor.id == target_user.id:
        raise PermissionDenied("This account cannot act on its own recovery request.")
    if target_user.role == CustomUser.Role.SUPER_ADMIN:
        if actor.role != CustomUser.Role.SUPER_ADMIN:
            raise PermissionDenied("Only a Super Admin may act on a Super Admin recovery request.")
        if CustomUser.objects.filter(role=CustomUser.Role.SUPER_ADMIN, is_active=True).count() < 2:
            raise ValidationError("Super Admin recovery needs at least two active Super Admins.")
    elif actor.role not in _PRIVILEGED_RECOVERY_ROLES:
        raise PermissionDenied("Only an administrator may act on an Admin recovery request.")


def recovery_dual_payload(req: RecoveryRequest) -> dict:
    return {
        "action": DUAL_RECOVERY_ACTION,
        "request_id": req.id,
        "target_employee_id": req.user.employee_id,
        "reason": req.reason,
    }


def pending_dual_recovery(req: RecoveryRequest):
    """The live first-approval record for this recovery request, if any."""
    from apps.accounts.models import IAMDualApprovalRequest

    return (
        IAMDualApprovalRequest.objects.filter(
            action=DUAL_RECOVERY_ACTION,
            target_id=str(req.id),
            status=IAMDualApprovalRequest.Status.PENDING,
            expires_at__gt=timezone.now(),
        )
        .select_related("requester")
        .first()
    )


def approve_privileged_recovery(request_id: int, session, base_url: str = ""):
    """Two-person approval for a privileged target.

    The first administrator's call (fresh Level 2 in their own IAM session) records a
    pending approval and nothing else: no token, no email. A different Super Admin,
    signed in separately with their own fresh Level 2 for the same action, completes it.
    Naming a person never counts as their approval. Returns ("pending"|"approved", req).
    """
    from apps.accounts.auth.iam_auth import (
        approve_dual_approval_request,
        compute_payload_digest,
        consume_dual_approval_request,
        create_dual_approval_request,
    )

    actor = session.iam_admin.user
    with transaction.atomic():
        req = (
            RecoveryRequest.objects.select_for_update()
            .filter(id=request_id, status=RecoveryRequest.Status.PENDING)
            .select_related("user")
            .first()
        )
        if not req:
            raise ValidationError("Recovery request not found or not in pending state.")
        target = req.user
        _check_privileged_recovery_role(actor, target)
        digest = compute_payload_digest(recovery_dual_payload(req))
        first = pending_dual_recovery(req)
        if first is not None and (
            first.requester_stepup_verified_at + IAM_LEVEL2_TTL <= timezone.now()
        ):
            # The first approver's own Level 2 has lapsed: that approval is stale.
            from apps.accounts.models import IAMDualApprovalRequest

            first.status = IAMDualApprovalRequest.Status.EXPIRED
            first.save(update_fields=["status"])
            first = None
        if first is None:
            create_dual_approval_request(
                session, DUAL_RECOVERY_ACTION, str(req.id), req.reason or "recovery", digest
            )
            log_auth_event(
                event="admin_recovery_first_approval",
                code="INFO",
                step="approve_admin_reset",
                employee_id=target.employee_id,
                reference_id=f"REQ-{req.id}",
                message=f"First approval by {actor.employee_id}; awaiting a different Super Admin",
            )
            return "pending", req
        if first.requester_id == session.iam_admin.id:
            raise PermissionDenied(
                "You already gave the first approval. A different Super Admin must approve."
            )
        if first.payload_digest != digest:
            raise ValidationError("The request changed after the first approval. Start again.")
        # Same authorized-approver set as check_recovery_authority: a Super Admin
        # target needs a Super Admin; an Admin target accepts an Admin or Super Admin.
        from apps.accounts.models import IAMAdminIdentity

        roles = (IAMAdminIdentity.Role.SUPER_ADMIN,)
        if target.role == CustomUser.Role.ADMIN:
            roles = (IAMAdminIdentity.Role.SUPER_ADMIN, IAMAdminIdentity.Role.ADMIN)
        approver_row = approve_dual_approval_request(session, first.request_id, allowed_roles=roles)
        consume_dual_approval_request(
            first.request_id, DUAL_RECOVERY_ACTION, str(req.id), digest, allowed_roles=roles
        )
        requester_user = approver_row.requester.user
        _check_privileged_recovery_role(requester_user, target)
        if approver_row.approver.user_id == target.id:
            raise PermissionDenied("The account being recovered cannot approve its own recovery.")
        req, raw = approve_administrator_reset(
            request_id=req.id,
            actor=requester_user,
            second_approver=actor,
            base_url=base_url,
            assurance_verified=True,
        )
        log_auth_event(
            event="admin_recovery_dual_completed",
            code="INFO",
            step="approve_admin_reset",
            employee_id=target.employee_id,
            reference_id=f"REQ-{req.id}",
            message=f"Approved by {requester_user.employee_id} and {actor.employee_id}",
        )
        return "approved", req


def reject_administrator_reset(
    request_id: int,
    actor: CustomUser,
    reason: str = "",
) -> RecoveryRequest:
    """Rejects an administrator recovery request."""
    from apps.accounts.auth.stepup import is_sudo_active

    if not is_sudo_active(actor):
        raise PermissionDenied("Recent sudo re-authentication required for recovery rejection.")

    with transaction.atomic():
        req = (
            RecoveryRequest.objects.select_for_update()
            .filter(id=request_id, status=RecoveryRequest.Status.PENDING)
            .select_related("user")
            .first()
        )
        if not req:
            raise ValidationError("Recovery request not found or not in pending state.")

        target_user = req.user
        if recovery_needs_dual_control(target_user):
            _check_privileged_recovery_role(actor, target_user)
        else:
            check_recovery_authority(actor, target_user)

        from apps.accounts.models import IAMDualApprovalRequest

        IAMDualApprovalRequest.objects.filter(
            action=DUAL_RECOVERY_ACTION,
            target_id=str(req.id),
            status=IAMDualApprovalRequest.Status.PENDING,
        ).update(status=IAMDualApprovalRequest.Status.REJECTED)

        req.status = RecoveryRequest.Status.REJECTED
        req.rejection_reason = reason or "Request rejected by administrator."
        req.approved_by = actor
        req.approved_at = timezone.now()
        req.save()

        if target_user.email and target_user.email_verified:
            notify_admin_reset_rejected(
                employee_id=target_user.employee_id,
                employee_email=target_user.email,
                reason=reason,
            )

        log_auth_event(
            event="admin_recovery_rejected",
            code="INFO",
            step="reject_admin_reset",
            employee_id=target_user.employee_id,
            reference_id=f"REQ-{req.id}",
            message=f"Recovery request rejected by {actor.employee_id}. Reason: {reason}",
        )

    return req


# ---------------------------------------------------------------------------
# Method 2 — Self-Service Password Reset
# ---------------------------------------------------------------------------


def self_service_reset_with_recovery_code(
    employee_id: str,
    recovery_code: str,
    new_password: str,
) -> CustomUser:
    """
    Method 2 Route A: Self-Service Reset using 1-time Backup Recovery Code.
    Atomically redeems recovery code, applies Argon2id password hash,
    invalidates outstanding reset tokens, revokes active sessions, and notifies verified email.
    """
    user = CustomUser.objects.filter(
        employee_id=employee_id.strip().upper(), is_active=True
    ).first()
    if not user:
        raise AuthException("AUTH-MFA-002", step="reset_recovery_code", employee_id=employee_id)

    # 1. Redeem recovery code atomically
    redeem_recovery_code(user, recovery_code)

    # 2. Validate new password
    validate_employee_password(new_password, user)

    # 3. Update password, revoke sessions, and invalidate active tokens
    with transaction.atomic():
        set_user_password(user, new_password)
        revoke_user_sessions(user)
        # Invalidate outstanding live reset tokens
        ActivationCode.objects.filter(
            user=user,
            purpose=ActivationCode.Purpose.PASSWORD_RESET,
            is_live=1,
        ).update(is_live=None)

        log_auth_event(
            event="password_reset_recovery_code",
            code="INFO",
            step="reset_recovery_code",
            employee_id=user.employee_id,
            message="Password reset successfully completed via recovery code; sessions revoked",
        )

        if user.email and user.email_verified:
            notify_password_changed(user.employee_id, user.email, method="One-Time Recovery Code")

    return user


def self_service_reset_with_totp(
    employee_id: str,
    totp_code: str,
    new_password: str,
) -> CustomUser:
    """
    Method 2 Route B: Self-Service Reset using current TOTP Authenticator Code.
    Validates TOTP against encrypted secret, enforces anti-replay, checks privileged recovery policy,
    applies Argon2id password hash, revokes sessions, and notifies verified email.
    """
    user = CustomUser.objects.filter(
        employee_id=employee_id.strip().upper(), is_active=True
    ).first()
    if not user:
        raise AuthException("AUTH-MFA-001", step="reset_totp", employee_id=employee_id)

    # Privileged account policy check: Super Admins cannot bypass dual-approval via TOTP alone
    if user.role == CustomUser.Role.SUPER_ADMIN:
        raise PermissionDenied(
            "Super Admin accounts require administrator dual-approval for password recovery."
        )

    # 1. Validate TOTP
    verify_totp_code(user, totp_code)

    # 2. Validate new password
    validate_employee_password(new_password, user)

    # 3. Update password, revoke sessions, invalidate tokens
    with transaction.atomic():
        set_user_password(user, new_password)
        revoke_user_sessions(user)
        ActivationCode.objects.filter(
            user=user,
            purpose=ActivationCode.Purpose.PASSWORD_RESET,
            is_live=1,
        ).update(is_live=None)

        log_auth_event(
            event="password_reset_totp",
            code="INFO",
            step="reset_totp",
            employee_id=user.employee_id,
            message="Password reset successfully completed via TOTP code; sessions revoked",
        )

        if user.email and user.email_verified:
            notify_password_changed(user.employee_id, user.email, method="TOTP Authenticator")

    return user


def request_self_service_password_reset(
    employee_id: str,
    email: str,
    recovery_code: Optional[str] = None,
    totp_code: Optional[str] = None,
    base_url: str = "",
) -> None:
    """
    Method 2: Reset Password Yourself.
    ONE single form with Employee ID, Recovery Code, TOTP Code, and Company Email.
    Validates company email domain (@salowtech.com).
    Enforces EXACTLY ONE proof (either recovery code OR totp code, never both or neither).
    Issues single-use token expiring in EXACTLY 30 SECONDS.
    """
    if not employee_id:
        raise ValidationError("Employee ID is required.")
    if not email:
        raise ValidationError("Company email is required.")

    clean_email = email.strip().lower()
    if not is_allowed_company_email(clean_email):
        raise ValidationError("Email address must belong to the @salowtech.com company domain.")

    rec_clean = (recovery_code or "").strip()
    totp_clean = (totp_code or "").strip()

    has_rec = bool(rec_clean)
    has_totp = bool(totp_clean)

    if not has_rec and not has_totp:
        raise ValidationError(
            "Please provide either a Recovery Code or an Authenticator (TOTP) Code."
        )
    if has_rec and has_totp:
        raise ValidationError(
            "Please provide either a Recovery Code or an Authenticator (TOTP) Code, not both."
        )

    normalized_id = employee_id.strip().upper()
    user = CustomUser.objects.filter(employee_id=normalized_id, is_active=True).first()

    if (
        not user
        or not user.email
        or user.email.strip().lower() != clean_email
        or not user.email_verified
    ):
        raise ValidationError(
            "Account verification failed. Please verify your Employee ID and registered company email."
        )

    if user.role == CustomUser.Role.SUPER_ADMIN:
        log_auth_event(
            event="password_reset_denied_privileged",
            code="AUTH-PERM-001",
            step="request_self_service_reset",
            employee_id=user.employee_id,
            message="Super Admin account cannot self-service reset password; admin dual-control required",
        )
        raise PermissionDenied(
            "Super Admin accounts require administrator dual-approval for password recovery."
        )

    # Verify and burn the single provided proof
    if has_rec:
        redeem_recovery_code(user, rec_clean)
    else:
        verify_totp_code(user, totp_clean)

    # Issue token with strict 30-second TTL
    _, raw_token = issue_email_token(
        user=user,
        purpose=ActivationCode.Purpose.PASSWORD_RESET,
        ttl_seconds=30,
    )

    reset_url = f"{base_url.rstrip('/')}/auth/recovery/email/reset/?token={raw_token}"
    notify_email_reset_link_requested(
        employee_id=user.employee_id,
        employee_email=user.email,
        reset_url=reset_url,
        expires_in="30 seconds",
    )

    log_auth_event(
        event="password_reset_link_dispatched",
        code="INFO",
        step="request_self_service_reset",
        employee_id=user.employee_id,
        message="Self-service password reset link dispatched (30s TTL)",
    )


def request_email_password_reset(employee_id: str, base_url: str) -> None:
    """
    Method 2 Route C: Request Password Reset Link via Verified Company Email.
    Safe neutral response: does not reveal whether the account exists or has verified email.
    If eligible and email verified, generates short-lived single-use token and queues email.
    """
    if not employee_id:
        return

    normalized_id = employee_id.strip().upper()
    user = CustomUser.objects.filter(employee_id=normalized_id, is_active=True).first()

    # Must be active and have verified company email
    if user and user.email and user.email_verified:
        # Privileged check: Super Admin accounts require admin dual control
        if user.role == CustomUser.Role.SUPER_ADMIN:
            log_auth_event(
                event="email_reset_denied_privileged",
                code="AUTH-PERM-001",
                step="request_email_reset",
                employee_id=user.employee_id,
                message="Super Admin account cannot self-service reset via email; admin dual control required",
            )
            return

        _, raw_token = issue_email_token(
            user=user,
            purpose=ActivationCode.Purpose.PASSWORD_RESET,
            ttl_minutes=15,
        )

        reset_url = f"{base_url.rstrip('/')}/auth/recovery/email/reset/?token={raw_token}"
        notify_email_reset_link_requested(
            employee_id=user.employee_id,
            employee_email=user.email,
            reset_url=reset_url,
        )


def verify_email_reset_token(raw_token: str) -> CustomUser:
    """
    Validates password reset email link without consuming it (GET safe).
    """
    _, user = verify_email_token_safe(
        raw_token=raw_token,
        purpose=ActivationCode.Purpose.PASSWORD_RESET,
    )
    return user


def complete_email_password_reset(raw_token: str, new_password: str) -> CustomUser:
    """
    Consumes password reset email token atomically and updates password.
    """
    _, user = consume_email_token(
        raw_token=raw_token,
        purpose=ActivationCode.Purpose.PASSWORD_RESET,
    )

    validate_employee_password(new_password, user)

    with transaction.atomic():
        set_user_password(user, new_password)
        revoke_user_sessions(user)
        # Mark approved recovery request completed if active
        RecoveryRequest.objects.filter(
            user=user,
            status=RecoveryRequest.Status.APPROVED,
        ).update(status=RecoveryRequest.Status.COMPLETED)
        # Invalidate any other live reset tokens
        ActivationCode.objects.filter(
            user=user,
            purpose=ActivationCode.Purpose.PASSWORD_RESET,
            is_live=1,
        ).update(is_live=None)

        log_auth_event(
            event="password_reset_email_completed",
            code="INFO",
            step="complete_email_reset",
            employee_id=user.employee_id,
            message="Password reset successfully completed via verified email token; sessions revoked",
        )

        if user.email and user.email_verified:
            notify_password_changed(user.employee_id, user.email, method="Verified Company Email")

    return user


# ---------------------------------------------------------------------------
# Method 3 — Lost or Replaced Authenticator Recovery
# ---------------------------------------------------------------------------


def request_authenticator_replacement(
    employee_id: str,
    current_password: str,
    email: str = "",
    base_url: str = "",
) -> bool:
    """
    Method 3: Recover Lost or Replaced Authenticator.
    Requires Employee ID, valid current password, and the matching verified company email.
    Creates pending MFA replacement request, issues single-use token expiring in 30 SECONDS,
    and queues verification email.
    """
    if not employee_id or not current_password:
        return False

    normalized_id = employee_id.strip().upper()
    user = CustomUser.objects.filter(employee_id=normalized_id, is_active=True).first()

    if not user or not user.check_password(current_password):
        log_auth_event(
            event="mfa_replacement_bad_password",
            code="AUTH-LOG-001",
            step="request_mfa_replacement",
            employee_id=normalized_id,
            message="Password verification failed for authenticator replacement request",
        )
        return False

    if not user.email or not user.email_verified:
        log_auth_event(
            event="mfa_replacement_no_verified_email",
            code="AUTH-ACT-001",
            step="request_mfa_replacement",
            employee_id=normalized_id,
            message="User lacks verified company email; cannot use self-service MFA replacement",
        )
        return False

    # The registered company email is part of the proof: it must be submitted and match.
    clean_email = (email or "").strip().lower()
    if not clean_email:
        log_auth_event(
            event="mfa_replacement_email_missing",
            code="AUTH-ACT-001",
            step="request_mfa_replacement",
            employee_id=normalized_id,
            message="Authenticator replacement requested without the company email",
        )
        return False
    if not is_allowed_company_email(clean_email):
        log_auth_event(
            event="mfa_replacement_invalid_domain",
            code="AUTH-ACT-001",
            step="request_mfa_replacement",
            employee_id=normalized_id,
            message=f"Email {clean_email} does not belong to allowed company domains",
        )
        return False
    if user.email.strip().lower() != clean_email:
        log_auth_event(
            event="mfa_replacement_email_mismatch",
            code="AUTH-ACT-001",
            step="request_mfa_replacement",
            employee_id=normalized_id,
            message="Submitted company email does not match registered employee email",
        )
        return False

    with transaction.atomic():
        # Record pending RecoveryRequest
        RecoveryRequest.objects.create(
            user=user,
            request_type=RecoveryRequest.RequestType.MFA_REPLACEMENT,
            status=RecoveryRequest.Status.PENDING,
            reason="Employee requested authenticator replacement via current password verification.",
        )

        _, raw_token = issue_email_token(
            user=user,
            purpose=ActivationCode.Purpose.MFA_REPLACEMENT,
            ttl_seconds=30,
        )

        verify_url = f"{base_url.rstrip('/')}/auth/recovery/mfa/verify/?token={raw_token}"
        notify_mfa_replacement_requested(
            employee_id=user.employee_id,
            employee_email=user.email,
            verification_url=verify_url,
        )

        log_auth_event(
            event="mfa_replacement_email_queued",
            code="INFO",
            step="request_mfa_replacement",
            employee_id=user.employee_id,
            message="Authenticator replacement verification email queued (30s TTL)",
        )

    return True


def verify_mfa_replacement_token(raw_token: str) -> CustomUser:
    """Safe read-only check of authenticator replacement email link (GET safe)."""
    _, user = verify_email_token_safe(
        raw_token=raw_token,
        purpose=ActivationCode.Purpose.MFA_REPLACEMENT,
    )
    return user


def authorize_mfa_replacement(raw_token: str) -> CustomUser:
    """
    Consumes MFA replacement email token (POST) and authorizes replacement state.
    Privileged accounts require administrator approval.
    """
    _, user = consume_email_token(
        raw_token=raw_token,
        purpose=ActivationCode.Purpose.MFA_REPLACEMENT,
    )

    # For Super Admin or Admin, check if administrative approval has occurred
    if user.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        req = RecoveryRequest.objects.filter(
            user=user,
            request_type=RecoveryRequest.RequestType.MFA_REPLACEMENT,
            status=RecoveryRequest.Status.APPROVED,
        ).first()
        if not req:
            raise PermissionDenied(
                "Privileged account authenticator replacement requires prior administrator approval."
            )

    log_auth_event(
        event="mfa_replacement_token_authorized",
        code="INFO",
        step="authorize_mfa_replacement",
        employee_id=user.employee_id,
        message="MFA replacement email token redeemed; ready for new authenticator enrollment",
    )
    return user


def complete_authenticator_replacement(user: CustomUser, totp_code: str) -> list[str]:
    """
    Finalizes authenticator replacement:
    1. Verifies code from the new pending authenticator.
    2. Activates new TOTP credential.
    3. Purges previous TOTP credential and revokes old recovery codes.
    4. Generates 10 new recovery codes and returns them for single display.
    5. Revokes all prior sessions.
    6. Sends security notification to verified company email.
    """
    from apps.accounts.auth.mfa_totp import verify_totp_code
    from apps.accounts.auth.recovery_codes import generate_ten_recovery_codes

    # 1. Verify code on new TOTP credential (allow unconfirmed)
    verify_totp_code(user, totp_code, allow_unconfirmed=True)

    with transaction.atomic():
        # 2. Generate 10 new recovery codes (this purges previous codes)
        new_recovery_codes = generate_ten_recovery_codes(user)

        # 3. Mark recovery request completed if one exists
        RecoveryRequest.objects.filter(
            user=user,
            request_type=RecoveryRequest.RequestType.MFA_REPLACEMENT,
            status__in=[RecoveryRequest.Status.PENDING, RecoveryRequest.Status.APPROVED],
        ).update(status=RecoveryRequest.Status.COMPLETED)

        # 4. Revoke prior sessions
        revoke_user_sessions(user)

        # 5. Security notification
        if user.email and user.email_verified:
            notify_mfa_replacement_success(user.employee_id, user.email)

        log_auth_event(
            event="mfa_replacement_completed",
            code="INFO",
            step="complete_mfa_replacement",
            employee_id=user.employee_id,
            message="Authenticator replacement completed; new TOTP and recovery codes active",
        )

    return new_recovery_codes


def finalize_authenticator_replacement(
    user: CustomUser, new_secret_b32: str, raw_codes: list[str]
) -> None:
    """Atomic commit of an authenticator replacement, run only after the new
    authenticator code was verified AND the user acknowledged the new recovery codes.
    Until this runs, the old authenticator and old recovery codes stay valid."""
    from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
    from apps.accounts.auth.recovery_codes import hash_recovery_code
    from apps.accounts.models import RecoveryCode, TOTPCredential

    ciphertext, nonce, key_id = encrypt_secret(new_secret_b32, get_auth_key_ring())
    with transaction.atomic():
        TOTPCredential.objects.update_or_create(
            user=user,
            defaults={
                "encrypted_secret": ciphertext,
                "nonce": nonce,
                "key_id": key_id,
                "confirmed_at": timezone.now(),
                "last_used_step": 0,
            },
        )
        RecoveryCode.objects.filter(user=user).delete()
        RecoveryCode.objects.bulk_create(
            [
                RecoveryCode(user=user, keyed_hash=hash_recovery_code(c), used_at=None)
                for c in raw_codes
            ]
        )
        RecoveryRequest.objects.filter(
            user=user,
            request_type=RecoveryRequest.RequestType.MFA_REPLACEMENT,
            status__in=[RecoveryRequest.Status.PENDING, RecoveryRequest.Status.APPROVED],
        ).update(status=RecoveryRequest.Status.COMPLETED)
        revoke_user_sessions(user)
        if user.email and user.email_verified:
            notify_mfa_replacement_success(user.employee_id, user.email)
        log_auth_event(
            event="mfa_replacement_completed",
            code="INFO",
            step="complete_mfa_replacement",
            employee_id=user.employee_id,
            message="Authenticator replacement committed after code check and acknowledgement",
        )


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
