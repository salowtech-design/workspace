"""Workspace idle timeout with a warning before sign-out.

Idle time is measured from the last real request or an explicit keep-alive. Background
polling (device approval checks, event feeds, the session status call itself) never counts as
activity, otherwise an open tab would never time out. The absolute session lifetime
(SESSION_COOKIE_AGE, 12 hours) is separate and unchanged.
"""
import time

from django.conf import settings

SESSION_KEY = "_salow_last_activity"
DEFAULT_IDLE_MINUTES = 15
DEFAULT_WARNING_SECONDS = 120

PASSIVE_PATH_PREFIXES = (
    "/auth/session/status",
    "/auth/devices/approval/pending-check",
    "/security/events",
)


def idle_limit_seconds() -> int:
    """Active workspace policy idle minutes (default 15), never below 1 minute."""
    minutes = getattr(settings, "WORKSPACE_IDLE_TIMEOUT_MINUTES", DEFAULT_IDLE_MINUTES)
    try:
        from apps.accounts.models import AuthenticationPolicy

        policy = (
            AuthenticationPolicy.objects.filter(domain=AuthenticationPolicy.Domain.WORKSPACE, is_active=True)
            .order_by("-version")
            .first()
        )
        if policy is not None:
            minutes = policy.idle_session_timeout_minutes
    except Exception:
        pass
    return max(60, int(minutes) * 60)


def warning_seconds(limit: int) -> int:
    configured = getattr(settings, "WORKSPACE_IDLE_WARNING_SECONDS", DEFAULT_WARNING_SECONDS)
    return max(0, min(int(configured), limit // 2))


def is_passive(path: str) -> bool:
    return path.startswith(PASSIVE_PATH_PREFIXES)


def touch(request, now: float = None) -> None:
    request.session[SESSION_KEY] = now if now is not None else time.time()


def seconds_remaining(request, now: float = None) -> int:
    """Seconds before idle sign-out; a session with no marker starts its clock now."""
    now = now if now is not None else time.time()
    last = request.session.get(SESSION_KEY)
    if last is None:
        touch(request, now)
        last = now
    return int(idle_limit_seconds() - (now - float(last)))
