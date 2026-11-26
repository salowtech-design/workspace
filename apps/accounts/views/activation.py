"""
First-Time Employee Activation Views.
Mandatory flow:
/activate -> /activate/password -> /activate/mfa/setup
          -> /activate/mfa/verify -> /activate/recovery-codes -> dashboard.
"""

from django.contrib import messages
from django.shortcuts import redirect, render

from apps.accounts.auth.activation import verify_and_consume_code
from apps.accounts.auth.employee_id import normalize_employee_id
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import get_or_create_pending_totp, verify_totp_code
from apps.accounts.auth.passwords import set_user_password
from apps.accounts.auth.recovery_codes import generate_ten_recovery_codes
from apps.accounts.auth.sessions import create_verified_session
from apps.accounts.models import CustomUser

ACTIVATION_SESSION_KEY = "_salow_act_user_id"


def activate_code_view(request):
    """Step 1: Enter Employee ID and password (or one-time activation code)."""
    if request.user.is_authenticated:
        return redirect("attendance:panel")

    error = None
    if request.method == "POST":
        raw_id = request.POST.get("employee_id", "")
        emp_id = normalize_employee_id(raw_id)
        submitted_cred = (
            request.POST.get("password") or request.POST.get("activation_code") or ""
        ).strip()
        ip_addr = request.META.get("REMOTE_ADDR")

        try:
            # First check if user exists and password matches
            # Stored ids are upper case (CustomUser.save enforces it), so the exact match is the
            # normal path. A row written around save() (a bulk write, raw SQL, older data) may be
            # stored in another case: fall back to the lookup this view used before, so it is
            # still found. On a case-sensitive column that fallback matches exactly as typed.
            user = CustomUser.objects.filter(employee_id=emp_id).first() or (
                CustomUser.objects.filter(employee_id__iexact=raw_id.strip()).first()
                if raw_id.strip()
                else None
            )
            if user and user.has_usable_password() and user.check_password(submitted_cred):
                request.session[ACTIVATION_SESSION_KEY] = user.id
                request.session.set_expiry(1800)  # 30 minute activation session
                from apps.accounts.models import TOTPCredential

                cred = TOTPCredential.objects.filter(user=user, confirmed_at__isnull=False).first()
                if cred:
                    # Section 7 A: If an already activated employee selects Login, direct them to Sign In
                    return redirect("/auth/login/?flow=signin")
                return redirect("accounts:activate_mfa_setup")

            # Fallback to one-time activation code verification
            _, user = verify_and_consume_code(
                employee_id=emp_id,
                raw_code=submitted_cred,
                ip_address=ip_addr,
            )
            request.session[ACTIVATION_SESSION_KEY] = user.id
            request.session.set_expiry(1800)  # 30 minute activation session
            return redirect("accounts:activate_password")
        except AuthException as e:
            error = e.user_message
        except Exception:
            error = "Invalid employee ID or password."

    return render(request, "accounts/activate/code.html", {"error": error})


def activate_password_view(request):
    """Step 2: Person chooses their own permanent password."""
    user_id = request.session.get(ACTIVATION_SESSION_KEY)
    if not user_id:
        messages.error(request, "Activation session expired. Please enter your code again.")
        return redirect("accounts:activate")

    user = CustomUser.objects.filter(id=user_id).first()
    if not user:
        return redirect("accounts:activate")

    error = None
    if request.method == "POST":
        pass1 = request.POST.get("password", "")
        pass2 = request.POST.get("confirm_password", "")

        if pass1 != pass2:
            error = "Passwords do not match."
        else:
            try:
                set_user_password(user, pass1)
                return redirect("accounts:activate_mfa_setup")
            except AuthException as e:
                error = e.user_message

    return render(
        request,
        "accounts/activate/password.html",
        {"target_user": user, "user": user, "error": error},
    )


def activate_mfa_setup_view(request):
    """Step 3: Setup authenticator app with pure SVG QR code and direct confirmation."""
    user_id = request.session.get(ACTIVATION_SESSION_KEY)
    if not user_id:
        return redirect("accounts:activate")

    user = CustomUser.objects.filter(id=user_id).first()
    if not user:
        return redirect("accounts:activate")

    secret, uri, svg_qr = get_or_create_pending_totp(user)
    # Format secret into groups of 4 for easy human typing
    grouped_secret = " ".join([secret[i : i + 4] for i in range(0, len(secret), 4)])

    error = None
    if request.method == "POST":
        code = request.POST.get("code", "").strip()
        try:
            verify_totp_code(user, code, allow_unconfirmed=True)
            return redirect("accounts:activate_recovery_codes")
        except AuthException as e:
            error = e.user_message

    return render(
        request,
        "accounts/activate/mfa_setup.html",
        {
            "user": user,
            "secret": grouped_secret,
            "raw_secret": secret,
            "svg_qr": svg_qr,
            "error": error,
        },
    )


def activate_mfa_verify_view(request):
    """Step 4: Enter first 6-digit authenticator code to confirm setup."""
    user_id = request.session.get(ACTIVATION_SESSION_KEY)
    if not user_id:
        return redirect("accounts:activate")

    user = CustomUser.objects.filter(id=user_id).first()
    if not user:
        return redirect("accounts:activate")

    error = None
    if request.method == "POST":
        code = request.POST.get("code", "")
        try:
            verify_totp_code(user, code, allow_unconfirmed=True)
            return redirect("accounts:activate_recovery_codes")
        except AuthException as e:
            error = e.user_message

    return render(request, "accounts/activate/mfa_verify.html", {"user": user, "error": error})


def activate_recovery_codes_view(request):
    """Step 5: View 10 recovery codes and activate account."""
    user_id = request.session.get(ACTIVATION_SESSION_KEY)
    if not user_id:
        return redirect("accounts:activate")

    user = CustomUser.objects.filter(id=user_id).first()
    if not user:
        return redirect("accounts:activate")

    if request.method == "POST":
        confirmed_saved = request.POST.get("confirmed_saved") == "1"
        if not confirmed_saved:
            messages.error(request, "You must check the box confirming you have saved these codes.")
        else:
            # Complete activation
            user.status = CustomUser.Status.ACTIVE
            user.save(update_fields=["status", "updated_at"])
            request.session.pop(ACTIVATION_SESSION_KEY, None)

            # Establish verified session
            create_verified_session(request, user)
            messages.success(request, f"Welcome to Salowtech Workspace, {user.full_name}!")
            return redirect("attendance:panel")

    recovery_codes = generate_ten_recovery_codes(user)
    return render(
        request,
        "accounts/activate/recovery_codes.html",
        {"user": user, "recovery_codes": recovery_codes},
    )
