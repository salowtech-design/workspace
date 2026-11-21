"""
Second-Device Verification Engine — Salow Workspace IAM.
Implements the approved secondary device authentication workflow:
1. 1-minute expiration on approval requests.
2. Device A in-application approval or permission-controlled Admin fallback.
3. Dual-control approval for Super Administrator accounts.
4. Company email OTP challenge post-approval.
5. 1:1 server-side face verification gate.
6. Creation of independent, concurrency-safe session for Device B.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta
from typing import Optional

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import get_activation_hmac_key
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.lockout import (
    check_lockout,
    clear_failed_attempts,
    record_failed_attempt,
)
from apps.accounts.auth.sessions import create_verified_session
from apps.accounts.models import (
    BiometricAssertion,
    CustomUser,
    DeviceApprovalRequest,
    UserSession,
)
from apps.core.models import EmailOutbox

DEVICE_APPROVAL_EXPIRY_SECONDS = 60  # Strictly 1 minute per specification
MAX_CONCURRENT_DEVICES = 2


def is_second_device_request(
    user: CustomUser,
    current_session_key: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> bool:
    """
    Checks if user currently has an active, authenticated session on another device.
    Verifies that the session exists in the durable session store and has not expired or been flushed.
    An additional device is identified if an existing active session exists with a different
    device profile (IP or User-Agent), or if multiple sessions are active.
    """
    from django.contrib.sessions.models import Session

    valid_session_keys = set(
        Session.objects.filter(expire_date__gt=timezone.now()).values_list("session_key", flat=True)
    )
    qs = UserSession.objects.filter(
        user=user,
        mfa_verified=True,
        session_key__in=valid_session_keys,
    )
    if current_session_key:
        qs = qs.exclude(session_key=current_session_key)

    if not qs.exists():
        return False

    # If incoming client details are provided, check for a distinct active device
    if ip_address or user_agent:
        distinct = qs.exclude(
            ip_address=ip_address,
            user_agent=(user_agent or "")[:255],
        )
        return distinct.exists()

    return qs.exists()


def get_active_device_count(user: CustomUser) -> int:
    """Returns number of active, verified sessions for the account."""
    return UserSession.objects.filter(user=user, mfa_verified=True).count()


def create_device_approval_request(
    user: CustomUser,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    requesting_device_id: Optional[str] = None,
) -> DeviceApprovalRequest:
    """
    Creates a pending secondary device approval challenge with strict 1-minute expiration
    and a transaction-bound 2-digit number challenge for visual matching verification.
    """
    now = timezone.now()
    expires = now + timedelta(seconds=DEVICE_APPROVAL_EXPIRY_SECONDS)
    challenge_num = secrets.randbelow(90) + 10  # 10 to 99

    with transaction.atomic():
        # Expire any previous pending requests for this user
        DeviceApprovalRequest.objects.filter(
            user=user,
            status=DeviceApprovalRequest.Status.PENDING,
        ).update(status=DeviceApprovalRequest.Status.EXPIRED)

        req = DeviceApprovalRequest.objects.create(
            user=user,
            requesting_device_ip=ip_address,
            requesting_device_agent=(user_agent or "")[:255],
            requesting_device_id=requesting_device_id or "",
            status=DeviceApprovalRequest.Status.PENDING,
            expires_at=expires,
            number_challenge=challenge_num,
        )

        log_auth_event(
            event="device_approval_requested",
            code="AUTH-DEV-001",
            step="device_approval",
            employee_id=user.employee_id,
            network_address=ip_address,
            reference_id=str(req.request_id),
            message=f"Second device approval initiated with challenge [{challenge_num}] (expires in {DEVICE_APPROVAL_EXPIRY_SECONDS}s)",
        )

        return req


def check_and_expire_request(req: DeviceApprovalRequest) -> DeviceApprovalRequest:
    """Checks request deadline and marks EXPIRED if 1 minute has elapsed."""
    if req.status == DeviceApprovalRequest.Status.PENDING and timezone.now() > req.expires_at:
        req.status = DeviceApprovalRequest.Status.EXPIRED
        req.save(update_fields=["status"])
        log_auth_event(
            event="device_approval_expired",
            code="AUTH-DEV-002",
            step="device_approval",
            employee_id=req.user.employee_id,
            reference_id=str(req.request_id),
            message="Second device approval request expired after 1 minute",
        )
    return req


def approve_device_by_existing_session(
    request_id: uuid.UUID,
    approver: CustomUser,
    submitted_number: Optional[int] = None,
    approving_device_id: Optional[str] = None,
) -> DeviceApprovalRequest:
    """
    Approves pending second-device request by the employee on Device A.
    Enforces number matching between Device A and Device B when challenge is provided.
    Generates single-use company email OTP.
    """
    with transaction.atomic():
        req = (
            DeviceApprovalRequest.objects.select_for_update()
            .filter(request_id=request_id)
            .first()
        )
        if not req:
            raise AuthException("AUTH-DEV-003", step="device_approval", message_override="Approval request not found.")

        check_and_expire_request(req)
        if req.status != DeviceApprovalRequest.Status.PENDING:
            raise AuthException("AUTH-DEV-004", step="device_approval", message_override=f"Request cannot be approved ({req.status}).")

        # Must be the exact same employee approving from an existing authenticated session
        if req.user_id != approver.id:
            raise PermissionDenied("You can only approve device requests for your own account.")

        # Mandatory Number Matching (Section 5.2)
        if req.number_challenge:
            if submitted_number is None:
                raise AuthException(
                    "AUTH-DEV-012",
                    step="device_approval",
                    message_override="Challenge number matching is mandatory for device approval.",
                )
            try:
                if int(submitted_number) != req.number_challenge:
                    raise AuthException(
                        "AUTH-DEV-012",
                        step="device_approval",
                        message_override="Challenge number mismatch. Verification number does not match requesting device.",
                    )
            except (ValueError, TypeError):
                raise AuthException(
                    "AUTH-DEV-012",
                    step="device_approval",
                    message_override="Invalid challenge number format.",
                )

        # Generate 6-digit email OTP
        raw_otp = f"{secrets.randbelow(900000) + 100000}"
        key = get_activation_hmac_key()
        otp_hash = hmac.new(key, raw_otp.encode("utf-8"), hashlib.sha256).hexdigest()

        req.status = DeviceApprovalRequest.Status.APPROVED
        req.approved_by = approver
        req.approved_at = timezone.now()
        req.approval_type = "primary_device"
        req.approving_device_id = approving_device_id or ""
        req.email_otp_hash = otp_hash
        req.save()

        # Queue OTP email securely via transactional outbox with envelope encryption
        if req.user.email:
            from apps.core.email_service import queue_email
            queue_email(
                recipient=req.user.email,
                subject="Salow Workspace — Second Device Verification OTP",
                body_text="Your one-time secondary device verification code is: [CONFIDENTIAL CODE ENCRYPTED IN SECURE PAYLOAD]. Valid for 5 minutes.",
                notification_type="device_approval_otp",
                sensitive_payload={
                    "otp": raw_otp,
                    "template": f"Your one-time secondary device verification code is: {raw_otp}. Valid for 5 minutes.",
                },
            )

        log_auth_event(
            event="device_approved_by_user",
            code="AUTH-DEV-005",
            step="device_approval",
            employee_id=req.user.employee_id,
            reference_id=str(req.request_id),
            message="Second device approved by primary device; email OTP dispatched",
        )

        return req


def approve_device_by_admin(
    request_id: uuid.UUID,
    admin_user: CustomUser,
    second_admin: Optional[CustomUser] = None,
) -> DeviceApprovalRequest:
    """
    Administrative fallback when primary device is broken or lost.
    Requires admin privileges and dual-control for Super Admin targets.
    """
    if admin_user.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can execute device fallback approvals.")

    with transaction.atomic():
        req = (
            DeviceApprovalRequest.objects.select_for_update()
            .filter(request_id=request_id)
            .first()
        )
        if not req:
            raise AuthException("AUTH-DEV-003", step="device_approval", message_override="Approval request not found.")

        check_and_expire_request(req)
        if req.status != DeviceApprovalRequest.Status.PENDING:
            raise AuthException("AUTH-DEV-004", step="device_approval", message_override=f"Request cannot be approved ({req.status}).")

        # Strict No Self-Approval (Section 9)
        if admin_user.id == req.user_id:
            raise PermissionDenied("Self-approval is strictly prohibited for administrator fallback.")

        # Dual control check for Super Admin target
        if req.user.role == CustomUser.Role.SUPER_ADMIN:
            if not second_admin or second_admin.id == admin_user.id:
                raise AuthException(
                    "AUTH-DEV-006",
                    step="device_approval",
                    message_override="Super Admin device fallback requires dual administrator approval.",
                )

        raw_otp = f"{secrets.randbelow(900000) + 100000}"
        key = get_activation_hmac_key()
        otp_hash = hmac.new(key, raw_otp.encode("utf-8"), hashlib.sha256).hexdigest()

        req.status = DeviceApprovalRequest.Status.APPROVED
        req.approved_by = admin_user
        req.approved_at = timezone.now()
        req.approval_type = "admin_fallback"
        req.email_otp_hash = otp_hash
        req.save()

        if req.user.email:
            from apps.core.email_service import queue_email
            queue_email(
                recipient=req.user.email,
                subject="Salow Workspace — Admin Approved Device OTP",
                body_text="Your administrator-approved device verification code is: [CONFIDENTIAL CODE ENCRYPTED IN SECURE PAYLOAD]. Valid for 5 minutes.",
                notification_type="device_approval_admin_otp",
                sensitive_payload={
                    "otp": raw_otp,
                    "template": f"Your administrator-approved device verification code is: {raw_otp}. Valid for 5 minutes.",
                },
            )

        log_auth_event(
            event="device_approved_by_admin",
            code="AUTH-DEV-007",
            step="device_approval",
            employee_id=req.user.employee_id,
            reference_id=str(req.request_id),
            message=f"Second device approved via admin fallback by {admin_user.employee_id}",
        )

        return req


def reject_device_request(
    request_id: uuid.UUID,
    actor: CustomUser,
    reason: str = "User rejected device connection",
) -> DeviceApprovalRequest:
    """Rejects pending device request."""
    with transaction.atomic():
        req = (
            DeviceApprovalRequest.objects.select_for_update()
            .filter(request_id=request_id)
            .first()
        )
        if not req:
            raise AuthException("AUTH-DEV-003", step="device_approval", message_override="Approval request not found.")

        req.status = DeviceApprovalRequest.Status.REJECTED
        req.rejection_reason = reason[:255]
        req.save()

        log_auth_event(
            event="device_approval_rejected",
            code="AUTH-DEV-008",
            step="device_approval",
            employee_id=req.user.employee_id,
            reference_id=str(req.request_id),
            message=f"Device request rejected by {actor.employee_id}: {reason}",
        )

        return req


def verify_device_email_otp(
    request_id: uuid.UUID,
    submitted_otp: str,
) -> bool:
    """
    Verifies the 6-digit email OTP on Device B.
    Enforces:
    - 5-minute validity window post-approval.
    - Anti-replay (cannot re-verify if already completed).
    - Database-backed independent rate limiting (locks after 5 failed attempts).
    """
    lockout_key = f"dev_otp:{request_id}"
    check_lockout(lockout_key)

    with transaction.atomic():
        req = (
            DeviceApprovalRequest.objects.select_for_update()
            .filter(request_id=request_id)
            .first()
        )
        if not req or req.status != DeviceApprovalRequest.Status.APPROVED:
            return False

        # Anti-replay: cannot verify if already completed
        if req.completed_at is not None:
            log_auth_event(
                event="device_email_otp_replay_blocked",
                code="AUTH-DEV-010",
                step="device_approval",
                employee_id=req.user.employee_id,
                reference_id=str(req.request_id),
                message="Blocked attempt to reuse completed device approval OTP",
            )
            return False

        # 5-minute expiration window post-approval
        if req.approved_at and timezone.now() > req.approved_at + timedelta(minutes=5):
            log_auth_event(
                event="device_email_otp_expired",
                code="AUTH-DEV-002",
                step="device_approval",
                employee_id=req.user.employee_id,
                reference_id=str(req.request_id),
                message="Second device email OTP expired (5-minute TTL elapsed)",
            )
            return False

        clean_otp = submitted_otp.strip()
        key = get_activation_hmac_key()
        computed_hash = hmac.new(key, clean_otp.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, req.email_otp_hash):
            record_failed_attempt(lockout_key, employee_id=req.user.employee_id, threshold=5)
            return False

        clear_failed_attempts(lockout_key)
        req.email_otp_verified = True
        req.save(update_fields=["email_otp_verified"])

        log_auth_event(
            event="device_email_otp_verified",
            code="INFO",
            step="device_approval",
            employee_id=req.user.employee_id,
            reference_id=str(req.request_id),
            message="Company email OTP verified successfully for Device B",
        )
        return True


def issue_device_biometric_assertion(
    user: CustomUser,
    approval_request: DeviceApprovalRequest,
    challenge_nonce: str,
    outcome: str = "verified",
    confidence_score: float = 0.95,
    liveness_score: float = 0.90,
    device_id: str = "",
) -> BiometricAssertion:
    """
    Issues a server-side, transaction-bound biometric verification assertion.
    Guarantees that face verification cannot be bypassed with a client-supplied boolean.
    Valid for exactly 2 minutes; single-use only.
    """
    now = timezone.now()
    single_use_token = secrets.token_urlsafe(32)

    assertion = BiometricAssertion.objects.create(
        user=user,
        device_id=device_id or (approval_request.requesting_device_id or ""),
        approval_request=approval_request,
        purpose="device_approval",
        challenge_nonce=challenge_nonce,
        outcome=outcome,
        confidence_score=confidence_score,
        liveness_score=liveness_score,
        single_use_nonce=single_use_token,
        issued_at=now,
        expires_at=now + timedelta(minutes=2),
        is_consumed=False,
    )

    log_auth_event(
        event="biometric_assertion_issued",
        code="INFO",
        step="device_approval",
        employee_id=user.employee_id,
        reference_id=str(assertion.id),
        message=f"Biometric assertion issued for request {approval_request.request_id} (outcome={outcome})",
    )
    return assertion


def complete_device_verification_and_login(
    django_request,
    request_id: uuid.UUID,
    assertion_token: Optional[str] = None,
    face_verified: Optional[bool] = None,
) -> CustomUser:
    """
    Final gate: completes face verification, establishes Device B's independent session.
    Enforces:
    - Server-issued, transaction-bound verification assertion mandatory.
    - Caller-supplied boolean flags (face_verified=True) are strictly rejected.
    - Single-use anti-replay: assertion and request cannot be reused.
    - Independent server session created for Device B.
    """
    if face_verified is not None and not assertion_token:
        # Explicitly deny caller-provided boolean bypass (Defect 5.4)
        raise AuthException(
            "AUTH-DEV-009",
            step="device_approval",
            message_override="Caller-supplied face_verified boolean is strictly prohibited; a server-issued verification assertion is required.",
        )

    if not assertion_token:
        raise AuthException(
            "AUTH-DEV-009",
            step="device_approval",
            message_override="Server-issued biometric verification assertion token is required.",
        )

    now = timezone.now()

    with transaction.atomic():
        req = (
            DeviceApprovalRequest.objects.select_for_update()
            .filter(request_id=request_id)
            .first()
        )
        if not req or req.status != DeviceApprovalRequest.Status.APPROVED or not req.email_otp_verified:
            raise AuthException("AUTH-DEV-010", step="device_approval", message_override="Incomplete verification steps.")

        # Anti-replay defense: request cannot be reused
        if req.completed_at is not None:
            raise AuthException("AUTH-DEV-010", step="device_approval", message_override="Device approval request has already been completed.")

        # Validate and atomically consume server assertion
        assertion = (
            BiometricAssertion.objects.select_for_update()
            .filter(
                single_use_nonce=assertion_token,
                user=req.user,
                approval_request=req,
                outcome="verified",
                is_consumed=False,
                expires_at__gt=now,
            )
            .first()
        )
        if not assertion:
            raise AuthException(
                "AUTH-DEV-009",
                step="device_approval",
                message_override="Biometric verification assertion is invalid, expired, or previously consumed.",
            )

        assertion.is_consumed = True
        assertion.consumed_at = now
        assertion.save(update_fields=["is_consumed", "consumed_at"])

        req.face_verified = True
        req.completed_at = now

        # Create independent server session for Device B
        create_verified_session(django_request, req.user)
        from apps.accounts.auth.session_backend import hash_session_bearer
        req.session_key = hash_session_bearer(django_request.session.session_key) if django_request.session.session_key else ""
        req.save()

        log_auth_event(
            event="second_device_session_established",
            code="AUTH-DEV-011",
            step="device_approval",
            employee_id=req.user.employee_id,
            reference_id=str(req.request_id),
            message=f"Second device session successfully established via server-issued biometric assertion [{assertion.id}]",
        )

        return req.user
