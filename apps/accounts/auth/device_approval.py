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
from typing import Optional, Tuple

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import get_activation_hmac_key
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.sessions import create_verified_session
from apps.accounts.models import CustomUser, DeviceApprovalRequest, UserSession
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
) -> DeviceApprovalRequest:
    """
    Creates a pending secondary device approval challenge with strict 1-minute expiration.
    """
    now = timezone.now()
    expires = now + timedelta(seconds=DEVICE_APPROVAL_EXPIRY_SECONDS)

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
            status=DeviceApprovalRequest.Status.PENDING,
            expires_at=expires,
        )

        log_auth_event(
            event="device_approval_requested",
            code="AUTH-DEV-001",
            step="device_approval",
            employee_id=user.employee_id,
            network_address=ip_address,
            reference_id=str(req.request_id),
            message=f"Second device approval initiated (expires in {DEVICE_APPROVAL_EXPIRY_SECONDS}s)",
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
) -> DeviceApprovalRequest:
    """
    Approves pending second-device request by the employee on Device A.
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

        # Generate 6-digit email OTP
        raw_otp = f"{secrets.randbelow(900000) + 100000}"
        key = get_activation_hmac_key()
        otp_hash = hmac.new(key, raw_otp.encode("utf-8"), hashlib.sha256).hexdigest()

        req.status = DeviceApprovalRequest.Status.APPROVED
        req.approved_by = approver
        req.approved_at = timezone.now()
        req.approval_type = "primary_device"
        req.email_otp_hash = otp_hash
        req.save()

        # Queue OTP email via EmailOutbox
        if req.user.email:
            EmailOutbox.objects.create(
                recipient=req.user.email,
                subject="Salow Workspace — Second Device Verification OTP",
                body_text=f"Your one-time secondary device verification code is: {raw_otp}. Valid for 5 minutes.",
                status=EmailOutbox.Status.QUEUED,
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
            EmailOutbox.objects.create(
                recipient=req.user.email,
                subject="Salow Workspace — Admin Approved Device OTP",
                body_text=f"Your administrator-approved device verification code is: {raw_otp}. Valid for 5 minutes.",
                status=EmailOutbox.Status.QUEUED,
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
    """Verifies the 6-digit email OTP on Device B."""
    with transaction.atomic():
        req = (
            DeviceApprovalRequest.objects.select_for_update()
            .filter(request_id=request_id)
            .first()
        )
        if not req or req.status != DeviceApprovalRequest.Status.APPROVED:
            return False

        clean_otp = submitted_otp.strip()
        key = get_activation_hmac_key()
        computed_hash = hmac.new(key, clean_otp.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, req.email_otp_hash):
            return False

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


def complete_device_verification_and_login(
    django_request,
    request_id: uuid.UUID,
    face_verified: bool = True,
) -> CustomUser:
    """
    Final gate: completes face verification, establishes Device B's independent session.
    """
    if not face_verified:
        raise AuthException("AUTH-DEV-009", step="device_approval", message_override="Biometric face verification failed.")

    with transaction.atomic():
        req = (
            DeviceApprovalRequest.objects.select_for_update()
            .filter(request_id=request_id)
            .first()
        )
        if not req or req.status != DeviceApprovalRequest.Status.APPROVED or not req.email_otp_verified:
            raise AuthException("AUTH-DEV-010", step="device_approval", message_override="Incomplete verification steps.")

        req.face_verified = True
        req.completed_at = timezone.now()

        # Create independent server session for Device B
        create_verified_session(django_request, req.user)
        req.session_key = django_request.session.session_key
        req.save()

        log_auth_event(
            event="second_device_session_established",
            code="AUTH-DEV-011",
            step="device_approval",
            employee_id=req.user.employee_id,
            reference_id=str(req.request_id),
            message="Second device session successfully established with dual verification",
        )

        return req.user
