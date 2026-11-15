"""
Server-Side Session Management & Revocation Engine.
Tracks active device sessions, rotates session keys, and handles revocation on security events.
"""

from datetime import timedelta

from django.contrib.auth import login
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import STAFF_SESSION_MAX_HOURS
from apps.accounts.models import CustomUser, UserSession


def create_verified_session(request, user: CustomUser) -> None:
    """
    Establish verified server-side session.
    Cycles session key to prevent session fixation and records UserSession row.
    """
    login(request, user)
    request.session.cycle_key()
    request.session["_salow_mfa_verified"] = True
    request.session.set_expiry(STAFF_SESSION_MAX_HOURS * 3600)

    session_key = request.session.session_key
    ip_addr = request.META.get("REMOTE_ADDR")
    agent = request.META.get("HTTP_USER_AGENT", "")[:255]

    if session_key:
        UserSession.objects.update_or_create(
            session_key=session_key,
            defaults={
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
        qs = qs.exclude(session_key=keep_session_key)

    count = qs.count()
    qs.delete()

    log_auth_event(
        event="sessions_revoked",
        code="INFO",
        step="revoke_sessions",
        employee_id=user.employee_id,
        message=f"Revoked {count} active sessions",
    )
    return count


def end_session_by_key(user: CustomUser, session_key: str) -> bool:
    """End a specific session from user's /security management page."""
    deleted, _ = UserSession.objects.filter(user=user, session_key=session_key).delete()
    return deleted > 0


def clean_stale_sessions() -> int:
    """Purge sessions older than maximum staff session duration."""
    cutoff = timezone.now() - timedelta(hours=STAFF_SESSION_MAX_HOURS)
    deleted, _ = UserSession.objects.filter(last_activity__lt=cutoff).delete()
    return deleted
