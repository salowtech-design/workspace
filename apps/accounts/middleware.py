from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.models import CustomUser


class AccountSecurityMiddleware:
    """
    Enforces real-time security rules on every authenticated request:
    1. Immediate invalidation if account status is no longer ACTIVE.
    2. Mandatory MFA enrollment redirect for privileged roles (Super Admin, Admin, Manager).
    """

    EXEMPT_PATH_PREFIXES = (
        "/auth/logout",
        "/auth/mfa",
        "/static/",
        "/health",
        "/ready",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            user: CustomUser = request.user

            # 1. Immediate termination of inactive/suspended/exited users
            if not user.is_active or user.status in (
                CustomUser.Status.SUSPENDED,
                CustomUser.Status.EXITED,
            ):
                logout(request)
                request.session.flush()
                return redirect(reverse("accounts:login"))

            # 2. Privileged role MFA gate
            path = request.path_info
            if user.role in (
                CustomUser.Role.SUPER_ADMIN,
                CustomUser.Role.ADMIN,
                CustomUser.Role.MANAGER,
            ):
                is_exempt = any(path.startswith(prefix) for prefix in self.EXEMPT_PATH_PREFIXES)
                totp_device = getattr(user, "totp_device", None)
                if not is_exempt and (not totp_device or not totp_device.is_confirmed):
                    return redirect(reverse("accounts:mfa_setup"))

        return self.get_response(request)
