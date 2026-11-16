from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.models import CustomUser, TOTPCredential


class AccountSecurityMiddleware:
    """
    Enforces real-time security invariant checks on every HTTP request:
    1. Invited and Activating accounts can never reach workspace pages.
    2. Disabled/Locked accounts are immediately ejected.
    3. Bootstrap Super Admin without confirmed MFA is strictly locked to MFA enrollment.
    """

    EXEMPT_PREFIXES = (
        "/auth/activate",
        "/auth/login",
        "/auth/mfa/challenge",
        "/auth/logout",
        "/health",
        "/ready",
        "/verify",
        "/static/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
            return self.get_response(request)

        if request.user.is_authenticated:
            user: CustomUser = request.user

            # 1. State enforcement: Invited and activating accounts can never access workspace pages
            if user.status in (CustomUser.Status.INVITED, CustomUser.Status.ACTIVATING):
                logout(request)
                request.session.flush()
                return redirect(reverse("accounts:activate"))

            # 2. Disabled or locked accounts cannot maintain an active session
            if not user.is_active or user.status in (
                CustomUser.Status.DISABLED,
                CustomUser.Status.LOCKED,
            ):
                logout(request)
                request.session.flush()
                return redirect(reverse("accounts:login"))

            # 3. Bootstrap Super Admin without MFA guard
            if user.role == CustomUser.Role.SUPER_ADMIN:
                has_mfa = TOTPCredential.objects.filter(
                    user=user, confirmed_at__isnull=False
                ).exists()
                if not has_mfa:
                    # Allow only activation MFA setup/verify endpoints
                    allowed_mfa_paths = (
                        "/auth/activate/mfa/setup",
                        "/auth/activate/mfa/verify",
                        "/auth/activate/recovery-codes",
                        "/auth/logout",
                        "/static/",
                    )
                    if not any(path.startswith(p) for p in allowed_mfa_paths):
                        request.session["_salow_act_user_id"] = user.id
                        return redirect(reverse("accounts:activate_mfa_setup"))

        return self.get_response(request)
