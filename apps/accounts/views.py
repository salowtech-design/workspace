from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.forms import LoginForm
from apps.accounts.models import CustomUser
from apps.accounts.permissions import is_sudo_active, require_permission, require_sudo
from apps.accounts.services import (
    admin_reset_password,
    change_password,
    confirm_totp_device,
    enroll_totp_device,
    login_user,
    logout_user,
    verify_recovery_code,
    verify_user_totp,
)


def login_view(request):
    """
    Renders login interface and processes initial credential validation.
    If MFA is active on account, routes to MFA challenge before establishing session.
    """
    if request.user.is_authenticated:
        return redirect("attendance:panel")

    redirect_to = request.POST.get("next") or request.GET.get("next") or ""

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data["user"]
            totp_device = getattr(user, "totp_device", None)

            # If user has confirmed MFA, route to MFA verification step
            if totp_device and totp_device.is_confirmed:
                request.session["_mfa_pending_user_id"] = user.id
                request.session["_mfa_next_url"] = redirect_to
                return redirect("accounts:mfa_verify")

            login_user(request, user)

            if url_has_allowed_host_and_scheme(
                url=redirect_to,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(redirect_to)
            return redirect("attendance:panel")
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form, "next": redirect_to})


def mfa_verify_view(request):
    """Verify 6-digit TOTP code or backup recovery code during login."""
    pending_user_id = request.session.get("_mfa_pending_user_id")
    if not pending_user_id:
        return redirect("accounts:login")

    user = CustomUser.objects.filter(id=pending_user_id, is_active=True).first()
    if not user:
        request.session.pop("_mfa_pending_user_id", None)
        return redirect("accounts:login")

    error = None
    if request.method == "POST":
        token = request.POST.get("token", "").strip()
        recovery_code = request.POST.get("recovery_code", "").strip()

        verified = False
        if token:
            verified = verify_user_totp(user, token)
        elif recovery_code:
            verified = verify_recovery_code(user, recovery_code)

        if verified:
            request.session.pop("_mfa_pending_user_id", None)
            next_url = request.session.pop("_mfa_next_url", "")
            login_user(request, user)
            if next_url and url_has_allowed_host_and_scheme(
                url=next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect("attendance:panel")
        else:
            error = "Invalid verification code or recovery token."

    return render(request, "accounts/mfa_verify.html", {"error": error})


@login_required
def mfa_setup_view(request):
    """Enroll or view TOTP multi-factor device."""
    user: CustomUser = request.user
    totp_device = getattr(user, "totp_device", None)

    if request.method == "POST":
        token = request.POST.get("token", "").strip()
        if confirm_totp_device(user, token):
            messages.success(request, "Multi-Factor Authentication successfully enrolled.")
            return redirect("attendance:panel")
        messages.error(request, "Invalid confirmation token. Please check authenticator clock.")

    device, uri, recovery_codes = enroll_totp_device(user)
    return render(
        request,
        "accounts/mfa_setup.html",
        {
            "device": device,
            "totp_uri": uri,
            "secret": device.secret_base32,
            "recovery_codes": recovery_codes,
            "is_confirmed": totp_device.is_confirmed if totp_device else False,
        },
    )


@login_required
def password_change_view(request):
    """Allow logged in user to update their password."""
    error = None
    if request.method == "POST":
        old_pass = request.POST.get("old_password", "")
        new_pass = request.POST.get("new_password", "")
        confirm_pass = request.POST.get("confirm_password", "")

        if new_pass != confirm_pass:
            error = "New passwords do not match."
        else:
            try:
                change_password(request.user, old_pass, new_pass, actor=request.user)
                messages.success(request, "Password updated successfully.")
                return redirect("attendance:panel")
            except ValidationError as e:
                error = e.messages[0] if hasattr(e, "messages") else str(e)

    return render(request, "accounts/password_change.html", {"error": error})


@login_required
@require_permission("accounts.user.edit")
@require_sudo
def admin_reset_password_view(request, employee_id: str):
    """Admin-initiated password reset with sudo gate."""
    target_user = CustomUser.objects.filter(employee_id=employee_id.upper()).first()
    if not target_user:
        messages.error(request, f"User {employee_id} not found.")
        return redirect("directory:list")

    if request.method == "POST":
        temp_pass = request.POST.get("temporary_password", "")
        try:
            admin_reset_password(request.user, target_user, temp_pass)
            messages.success(request, f"Password reset for {target_user.employee_id}.")
            return redirect("directory:profile", employee_id=target_user.employee_id)
        except ValidationError as e:
            messages.error(request, str(e))

    return render(
        request,
        "accounts/admin_password_reset.html",
        {"target_user": target_user, "is_sudo": is_sudo_active(request)},
    )


def logout_view(request):
    """Enforces POST-only session invalidation."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"], "Logout must be requested via POST.")

    logout_user(request)
    return redirect("accounts:login")
