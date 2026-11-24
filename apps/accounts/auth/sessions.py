"""
Server-Side Session Management & Revocation Engine.
Tracks active device sessions, rotates session keys, and handles revocation on security events.
"""

from datetime import timedelta

from django.contrib.auth import login
from django.contrib.sessions.models import Session
from django.db.models import Q
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import STAFF_SESSION_MAX_HOURS
from apps.accounts.auth.session_backend import hash_session_bearer
from apps.accounts.models import CustomUser, UserSession


def create_verified_session(request, user: CustomUser) -> None:
    """
    Establish verified server-side session.
    Cycles session key to prevent session fixation and records UserSession row with digest.
    """
    login(request, user)
    request.session.cycle_key()
    request.session["_salow_mfa_verified"] = True
    request.session.set_expiry(STAFF_SESSION_MAX_HOURS * 3600)

    session_key = request.session.session_key
    ip_addr = getattr(request, "client_ip", None) or request.META.get("REMOTE_ADDR")
    agent = request.META.get("HTTP_USER_AGENT", "")[:255]

    if session_key:
        digest = hash_session_bearer(session_key)
        UserSession.objects.update_or_create(
            session_key=digest,
            defaults={
                "session_digest": digest,
                "user": user,
                "ip_address": ip_addr,
                "user_agent": agent,
                "mfa_verified": True,
            },
        )

    log_auth_event(
        event="session_established",
        code="INFO",
        step="create_session",
        employee_id=user.employee_id,
        network_address=ip_addr,
        message=f"Session established for {user.employee_id} (MFA Verified)",
    )


def revoke_user_sessions(user: CustomUser, keep_session_key: str = None) -> int:
    """
    Revoke all active sessions for a user (e.g. on password change, role elevation, or disable).
    Optionally keep the current initiating session.
    """
    qs = UserSession.objects.filter(user=user)
    if keep_session_key:
        keep_digest = hash_session_bearer(keep_session_key)
        qs = qs.exclude(
            Q(session_key=keep_session_key)
            | Q(session_key=keep_digest)
            | Q(session_digest=keep_digest)
        )

    keys_to_purge = list(qs.values_list("session_key", flat=True))
    digests_to_purge = list(qs.values_list("session_digest", flat=True))
    count = qs.count()
    qs.delete()

    all_purge = set(keys_to_purge + digests_to_purge)
    all_purge.discard("")
    if all_purge:
        Session.objects.filter(session_key__in=all_purge).delete()

    from apps.accounts.auth.stepup import _USER_BREAK_GLASS_CACHE, _USER_SUDO_CACHE

    _USER_SUDO_CACHE.pop(user.id, None)
    _USER_BREAK_GLASS_CACHE.pop(user.id, None)

    log_auth_event(
        event="sessions_revoked",
        code="INFO",
        step="revoke_sessions",
        employee_id=user.employee_id,
        message=f"Revoked {count} active sessions",
    )
    return count


def synchronize_employee_lifecycle_revocation(user: CustomUser) -> dict:
    """
    Synchronously revokes all active authorities, sessions, pending approvals,
    and recovery requests across both Workspace and IAM domains when an employee
    is suspended, exited, or locked out.
    """
    revoked_stats = {
        "workspace_sessions": 0,
        "iam_sessions": 0,
        "device_requests": 0,
        "recovery_requests": 0,
        "dual_approvals": 0,
    }

    # 1. Revoke all Workspace sessions
    revoked_stats["workspace_sessions"] = revoke_user_sessions(user)

    # 2. Revoke all IAM sessions and suspend IAM identity if linked
    from apps.accounts.models import (
        DeviceApprovalRequest,
        IAMAdminIdentity,
        IAMDualApprovalRequest,
        IAMSession,
        RecoveryRequest,
    )

    try:
        if hasattr(user, "iam_admin_profile") and user.iam_admin_profile:
            iam_admin = user.iam_admin_profile
            active_iam_sessions = IAMSession.objects.filter(iam_admin=iam_admin, is_active=True)
            revoked_stats["iam_sessions"] = active_iam_sessions.count()
            active_iam_sessions.update(is_active=False)

            if user.status != CustomUser.Status.ACTIVE:
                iam_admin.status = IAMAdminIdentity.Status.SUSPENDED
                iam_admin.save(update_fields=["status"])
    except Exception:
        pass

    # 3. Reject pending DeviceApprovalRequests
    revoked_stats["device_requests"] = DeviceApprovalRequest.objects.filter(
        user=user, status=DeviceApprovalRequest.Status.PENDING
    ).update(status=DeviceApprovalRequest.Status.REJECTED)

    # 4. Reject pending RecoveryRequests
    revoked_stats["recovery_requests"] = RecoveryRequest.objects.filter(
        user=user, status=RecoveryRequest.Status.PENDING
    ).update(status=RecoveryRequest.Status.REJECTED)

    # 5. Expire pending IAMDualApprovalRequests where user is requester or approver
    revoked_stats["dual_approvals"] = IAMDualApprovalRequest.objects.filter(
        Q(requester__user=user) | Q(approver__user=user),
        status="pending",
    ).update(status="expired")

    log_auth_event(
        event="employee_lifecycle_revocation_synchronized",
        code="WARN",
        step="lifecycle_synchronization",
        employee_id=user.employee_id,
        message=f"Lifecycle revocation synchronized: {revoked_stats}",
    )

    return revoked_stats


def end_session_by_key(user: CustomUser, session_key: str) -> bool:
    """End a specific session from user's /security management page."""
    digest = hash_session_bearer(session_key)
    deleted, _ = (
        UserSession.objects.filter(user=user)
        .filter(Q(session_key=session_key) | Q(session_key=digest) | Q(session_digest=digest))
        .delete()
    )
    if deleted > 0:
        Session.objects.filter(session_key__in=[session_key, digest]).delete()
        return True
    return False


def clean_stale_sessions() -> int:
    """Purge sessions older than maximum staff session duration."""
    cutoff = timezone.now() - timedelta(hours=STAFF_SESSION_MAX_HOURS)
    deleted, _ = UserSession.objects.filter(last_activity__lt=cutoff).delete()
    return deleted
