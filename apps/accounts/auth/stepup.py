"""
Step-Up (Sudo Mode) Authentication Engine.
Enforces 10-minute re-authentication window for sensitive operations.
Super admin sensitive actions additionally require a fresh authenticator code.
"""

from functools import wraps
from typing import Callable, Optional

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import SUDO_WINDOW_MINUTES
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import verify_totp_code
from apps.accounts.models import CustomUser

SUDO_GRANTED_TIMESTAMP_KEY = "_salow_sudo_granted_at"


def is_sudo_active(request) -> bool:
    """Check if current session has active sudo authorization within last 10 minutes."""
    if not request.user.is_authenticated:
        return False
    granted_at = request.session.get(SUDO_GRANTED_TIMESTAMP_KEY)
    if not granted_at:
        return False
    elapsed_seconds = timezone.now().timestamp() - granted_at
    return elapsed_seconds < (SUDO_WINDOW_MINUTES * 60)


def grant_sudo(request, user: Optional[CustomUser] = None) -> None:
    """Mark session as holding valid sudo authorization."""
    target_user = user or getattr(request, "user", None)
    request.session[SUDO_GRANTED_TIMESTAMP_KEY] = timezone.now().timestamp()
    if target_user and getattr(target_user, "is_authenticated", False):
        log_auth_event(
            event="sudo_granted",
            code="INFO",
            step="stepup",
            employee_id=target_user.employee_id,
            network_address=request.META.get("REMOTE_ADDR"),
            message=f"Sudo mode granted for {SUDO_WINDOW_MINUTES} minutes",
        )


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
