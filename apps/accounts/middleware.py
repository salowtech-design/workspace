from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden, HttpResponseNotFound
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.auth.iam_auth import get_iam_session_from_request
from apps.accounts.models import CustomUser, TOTPCredential


class HostSecurityIsolationMiddleware:
    """
    Enforces Non-Negotiable Architecture (Section 3):
    Two separate security authorities within the existing Django modular monolith:
    1. Workspace Security operates on "app.salowtech.com"
       - Controls employee access, workspace passwords, workspace sessions (__Host-workspace_session).
    2. Central IAM operates on "iam.salowtech.com"
       - Dedicated IAM administrator identities, passwords, and sessions (__Host-iam_session).
    3. Strict Isolation:
       - Workspace session cannot authenticate IAM.
       - IAM session cannot authenticate Workspace.
       - Requests to each hostname cannot cross-authenticate.
    """

    IAM_HOST = getattr(settings, "IAM_HOSTNAME", "iam.salowtech.com")
    WORKSPACE_HOST = getattr(settings, "WORKSPACE_HOSTNAME", "app.salowtech.com")

    EXEMPT_PREFIXES = (
        "/health",
        "/ready",
        "/static/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
            return self.get_response(request)

        host = request.get_host().split(":")[0].lower()

        # -------------------------------------------------------------------
        # 1. IAM Host Authority (iam.salowtech.com)
        # -------------------------------------------------------------------
        if host == self.IAM_HOST.lower():
            request.urlconf = "config.urls_iam"
            request.is_iam_host = True

            # Resolve dedicated IAM session
            iam_session = get_iam_session_from_request(request)
            request.iam_session = iam_session
            request.iam_admin = iam_session.iam_admin if iam_session else None

            # Workspace application modules are strictly prohibited on iam.salowtech.com
            workspace_prefixes = ("/attendance", "/posts", "/directory", "/certificates")
            if any(path.startswith(prefix) for prefix in workspace_prefixes):
                return HttpResponseNotFound("Workspace applications are not accessible on iam.salowtech.com.")

            # Root path redirects to IAM console or login
            if path in ("/", ""):
                if iam_session:
                    return redirect("iam:dashboard")
                return redirect("iam:login")

            # Allow public IAM endpoints
            if path.startswith(("/iam/login", "/login", "/auth/login")):
                return self.get_response(request)

            # Require active IAM session for all other IAM host endpoints
            if not iam_session:
                login_url = reverse("iam:login", urlconf="config.urls_iam")
                return redirect(f"{login_url}?next={path}")

            return self.get_response(request)

        # -------------------------------------------------------------------
        # 2. Workspace Host Authority (app.salowtech.com)
        # -------------------------------------------------------------------
        if host == self.WORKSPACE_HOST.lower():
            request.urlconf = "config.urls_workspace"
            request.is_iam_host = False

            # On Workspace host, direct access to root IAM is segregated
            if path.startswith("/iam/"):
                return redirect(f"https://{self.IAM_HOST}{path}")

            return self.get_response(request)

        # -------------------------------------------------------------------
        # 3. Development / Testserver Fallback
        # -------------------------------------------------------------------
        request.is_iam_host = path.startswith("/iam/")
        if request.is_iam_host:
            iam_session = get_iam_session_from_request(request)
            request.iam_session = iam_session
            request.iam_admin = iam_session.iam_admin if iam_session else None

        return self.get_response(request)


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
        "/auth/recovery",
        "/auth/logout",
        "/iam/",
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

            # 2. Disabled, locked, suspended, or exited accounts cannot maintain an active session
            if not user.is_active or user.status in (
                CustomUser.Status.DISABLED,
                CustomUser.Status.LOCKED,
                CustomUser.Status.SUSPENDED,
                CustomUser.Status.EXITED,
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
