"""
Everyday Login & MFA Challenge Views.
Mandatory flow:
/login -> /mfa/challenge -> workspace dashboard.
STRICT REQUIREMENT: The MFA challenge page must NOT display a QR code.
"""

from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.login_flow import (
    authenticate_password,
    complete_mfa_challenge,
)
from apps.accounts.auth.sessions import revoke_user_sessions
from apps.accounts.models import CustomUser, TOTPCredential

PENDING_LOGIN_USER_ID_KEY = "_salow_login_pending_uid"
LOGIN_NEXT_URL_KEY = "_salow_login_next_url"


def login_view(request):
    """Step 1 of everyday login: Employee ID and password entry."""
    if request.user.is_authenticated:
        return redirect("attendance:panel")

    redirect_to = request.POST.get("next") or request.GET.get("next") or ""
    error = None

    if request.method == "POST":
        emp_id = request.POST.get("employee_id", "").strip()
        password = request.POST.get("password", "")
        ip_addr = request.META.get("REMOTE_ADDR")

        try:
            user = authenticate_password(emp_id, password, ip_address=ip_addr)

            # Check if user has completed MFA setup
            cred = TOTPCredential.objects.filter(user=user, confirmed_at__isnull=False).first()
            if not cred:
                # First time login without MFA: route to MFA enrollment (shows QR code, secret key, recovery codes)
                request.session["_salow_act_user_id"] = user.id
                return redirect("accounts:activate_mfa_setup")

            request.session[PENDING_LOGIN_USER_ID_KEY] = user.id
            request.session[LOGIN_NEXT_URL_KEY] = redirect_to
            return redirect("accounts:mfa_challenge")
        except AuthException as e:
            error = e.user_message

    return render(
        request,
        "accounts/login/login.html",
        {"error": error, "next": redirect_to},
    )


def mfa_challenge_view(request):
    """
    Step 2 of everyday login: 6-digit authenticator challenge or recovery code.
    Strictly NO QR CODE is rendered on this page.
    """
    pending_uid = request.session.get(PENDING_LOGIN_USER_ID_KEY)
    if not pending_uid:
        return redirect("accounts:login")

    user = CustomUser.objects.filter(id=pending_uid, is_active=True).first()
    if not user:
        request.session.pop(PENDING_LOGIN_USER_ID_KEY, None)
        return redirect("accounts:login")

    error = None
    if request.method == "POST":
        totp_code = request.POST.get("token", "").strip()
        recovery_code = request.POST.get("recovery_code", "").strip()

        try:
            if recovery_code:
                complete_mfa_challenge(request, user, recovery_code, is_recovery_code=True)
            elif totp_code:
                complete_mfa_challenge(request, user, totp_code, is_recovery_code=False)
            else:
                error = (
                    "Please enter either your 6-digit authenticator code or a backup recovery code."
                )

            if not error:
                next_url = request.session.pop(LOGIN_NEXT_URL_KEY, "")
                request.session.pop(PENDING_LOGIN_USER_ID_KEY, None)
                if next_url and url_has_allowed_host_and_scheme(
                    url=next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(next_url)
                return redirect("attendance:panel")
        except AuthException as e:
            error = e.user_message

    return render(
        request,
        "accounts/login/challenge.html",
        {"user": user, "error": error},
    )


def logout_view(request):
    """Enforce POST-only session invalidation."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"], "Logout must be requested via POST.")

    if request.user.is_authenticated:
        revoke_user_sessions(request.user, keep_session_key=None)

    from django.contrib.auth import logout

    logout(request)
    request.session.flush()
    return redirect("accounts:login")
