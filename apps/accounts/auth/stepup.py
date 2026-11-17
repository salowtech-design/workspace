"""
Step-Up (Sudo Mode) Authentication Engine.
Enforces 10-minute re-authentication window for sensitive operations.
Super admin sensitive actions additionally require a fresh authenticator code.
"""

from functools import wraps
from typing import Callable, Optional, Union

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import SUDO_WINDOW_MINUTES
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import verify_totp_code
from apps.accounts.models import CustomUser

SUDO_GRANTED_TIMESTAMP_KEY = "_salow_sudo_granted_at"
_USER_SUDO_CACHE: dict[int, float] = {}


def is_sudo_active(target: Union[object, CustomUser]) -> bool:
    """
    Check if current session or user has active sudo authorization within last 10 minutes.
    Accepts either an HttpRequest or a CustomUser instance.
    """
    now_ts = timezone.now().timestamp()
    window_sec = SUDO_WINDOW_MINUTES * 60

    # If a CustomUser was passed directly
    if isinstance(target, CustomUser):
        if not target.is_authenticated:
            return False
        cached_ts = _USER_SUDO_CACHE.get(target.id)
        if cached_ts and (now_ts - cached_ts < window_sec):
            return True
        return False

    # If HttpRequest was passed
    request = target
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False

    if hasattr(request, "session") and SUDO_GRANTED_TIMESTAMP_KEY in request.session:
        granted_at = request.session.get(SUDO_GRANTED_TIMESTAMP_KEY)
        if granted_at is not None:
            return (now_ts - granted_at) < window_sec
        return False

    # Fallback to in-process cache by user ID (e.g. CLI or elevate_sudo(user) calls)
    cached_ts = _USER_SUDO_CACHE.get(request.user.id)
    if cached_ts and (now_ts - cached_ts < window_sec):
        return True

    return False


def grant_sudo(request_or_user, user: Optional[CustomUser] = None) -> None:
    """Mark session and/or user as holding valid sudo authorization."""
    now_ts = timezone.now().timestamp()
    target_user = user

    if isinstance(request_or_user, CustomUser):
        target_user = request_or_user
        _USER_SUDO_CACHE[target_user.id] = now_ts
    else:
        request = request_or_user
        if hasattr(request, "session"):
            request.session[SUDO_GRANTED_TIMESTAMP_KEY] = now_ts
        if not target_user and hasattr(request, "user"):
            target_user = request.user
        if target_user and getattr(target_user, "is_authenticated", False):
            _USER_SUDO_CACHE[target_user.id] = now_ts

    if target_user and getattr(target_user, "is_authenticated", False):
        remote_ip = None
        if hasattr(request_or_user, "META"):
            remote_ip = request_or_user.META.get("REMOTE_ADDR")
        log_auth_event(
            event="sudo_granted",
            code="INFO",
            step="stepup",
            employee_id=target_user.employee_id,
            network_address=remote_ip,
            message=f"Sudo mode granted for {SUDO_WINDOW_MINUTES} minutes",
        )


def elevate_sudo(target_user_or_request) -> None:
    """Convenience helper to grant sudo elevation in tests and operational workflows."""
    grant_sudo(target_user_or_request)


def verify_stepup(request, password: str, totp_code: str = None) -> bool:
    """
    Authenticate user for step-up elevation.
    If user is Super Admin, a fresh 6-digit authenticator code is strictly required.
    """
    user: CustomUser = request.user
    if not user.is_authenticated:
        raise AuthException("AUTH-SES-001", step="stepup")

    # 1. Password verification
    if not user.check_password(password):
        log_auth_event(
            event="sudo_password_failed",
            code="AUTH-STP-001",
            step="stepup",
            employee_id=user.employee_id,
            network_address=request.META.get("REMOTE_ADDR"),
            message="Invalid password during step-up elevation",
        )
        raise AuthException(
            "AUTH-STP-001",
            step="stepup",
            employee_id=user.employee_id,
            message_override="Password verification failed for step-up action.",
        )

    # 2. Super Admin extra check: requires fresh authenticator code
    if user.role == CustomUser.Role.SUPER_ADMIN:
        if not totp_code:
            raise AuthException(
                "AUTH-STP-001",
                step="stepup",
                employee_id=user.employee_id,
                message_override="Super Admin step-up requires a fresh authenticator code.",
            )
        verify_totp_code(user, totp_code)

    grant_sudo(request, user)
    return True


def require_sudo(view_func: Callable):
    """View decorator ensuring sudo password re-entry is active."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_sudo_active(request):
            raise PermissionDenied("Step-up authentication required: please re-enter password.")
        return view_func(request, *args, **kwargs)

    return _wrapped
