"""
Account Recovery & Password Reset Views for Salow Workspace IAM.
Implements the 3 authoritative top-level recovery methods:
1. Method 1 — Request Administrator Reset
2. Method 2 — Reset Password Yourself (Route A: Recovery Code, Route B: TOTP, Route C: Verified Email Link)
3. Method 3 — Recover Lost or Replaced Authenticator
Includes token continuation screens (GET does not consume; explicit POST redemption),
safe URL redirection without bearer tokens, and administrative approval console.
"""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.auth.activation import (
    consume_email_token,
    verify_email_token_safe,
)
from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import (
    generate_qr_svg,
    get_or_create_pending_totp,
)
from apps.accounts.auth.passkeys import verify_passkey_authentication
from apps.accounts.auth.passwords import set_user_password, validate_employee_password
from apps.accounts.auth.reset import (
    approve_administrator_reset,
    complete_authenticator_replacement,
    reject_administrator_reset,
    request_administrator_reset,
    request_authenticator_replacement,
    request_self_service_password_reset,
    verify_email_reset_token,
    verify_mfa_replacement_token,
    verify_mfa_reset_and_authorize,
)
from apps.accounts.auth.sessions import create_verified_session, revoke_user_sessions
from apps.accounts.auth.stepup import is_sudo_active
from apps.accounts.models import ActivationCode, CustomUser, RecoveryRequest
from apps.accounts.permissions import (
    PERM_RECOVERY_ADMIN,
    PERM_RECOVERY_DELEGATED,
    has_permission,
)

PW_RESET_SESSION_UID = "_salow_pw_reset_user_id"
MFA_REPLACE_SESSION_UID = "_salow_mfa_replace_uid"
MFA_REPLACE_CODES_KEY = "_salow_mfa_replace_codes"


LEGACY_RECOVERY_ACTIONS = frozenset(
    {"verify_recovery_code", "verify_totp_code", "send_email_reset_link"}
)


def forgot_password_view(request):  # noqa: C901
    """
    Top-level forgot password recovery hub.
    Exposes exactly three distinct recovery methods:
    1. Method 1: Request Administrator Reset
    2. Method 2: Reset Password Yourself (Route A: Recovery Code, Route B: TOTP, Route C: Email Link)
    3. Method 3: Recover Lost or Replaced Authenticator
    Never displays all input fields simultaneously. Uses shared Employee ID context.
    """
    if request.user.is_authenticated:
        return redirect("attendance:panel")

    error = None
    success_msg = None

    # Shared Employee ID prefill context
    employee_id = (
        (
            request.GET.get("employee_id")
            or request.POST.get("employee_id")
            or request.session.get("_salow_login_emp_id", "")
        )
        .strip()
        .upper()
    )

    active_method = request.GET.get("method") or request.POST.get("method") or "method1"
    sub_route = request.GET.get("route") or request.POST.get("route") or "recovery_code"

    base_url = request.build_absolute_uri("/")[:-1]

    if request.method == "POST":
        action = request.POST.get("action", "")

        # -------------------------------------------------------------------
        # METHOD 1 — Request Administrator Reset
        # -------------------------------------------------------------------
        if action == "request_admin_reset":
            active_method = "method1"
            if not employee_id:
                error = "Please enter your Employee ID."
            else:
                reason = request.POST.get("reason", "")
                request_administrator_reset(employee_id=employee_id, reason=reason)
                success_msg = (
                    "If an eligible active account matches this Employee ID, "
                    "a recovery request has been routed to the administrator queue."
                )

        # -------------------------------------------------------------------
        # METHOD 2 — Reset Password Yourself (Single Unified Form)
        # -------------------------------------------------------------------
        elif action == "reset_password_yourself":
            active_method = "method2"
            rec_code = request.POST.get("recovery_code", "").strip()
            totp_in = request.POST.get("totp_code", "").strip()
            comp_email = request.POST.get("email", "").strip()

            try:
                request_self_service_password_reset(
                    employee_id=employee_id,
                    email=comp_email,
                    recovery_code=rec_code,
                    totp_code=totp_in,
                    base_url=base_url,
                )
                success_msg = (
                    "Verification link has been sent to your registered company email. "
                    "For security, the link is valid for exactly 30 seconds."
                )
            except (ValidationError, AuthException, PermissionDenied) as e:
                error = e.messages[0] if hasattr(e, "messages") else str(e)

        # The older single-factor routes (recovery code only, TOTP only, email link only) were
        # removed: the documented Method 2 needs exactly one proof AND the company email.
        elif action in LEGACY_RECOVERY_ACTIONS:
            error = (
                "This recovery action is not available. Use one of the three options on this page."
            )

        # -------------------------------------------------------------------
        # METHOD 3 — Recover Lost or Replaced Authenticator
        # -------------------------------------------------------------------
        elif action == "send_mfa_replacement_email":
            active_method = "method3"
            current_password = request.POST.get("current_password", "")
            comp_email = request.POST.get("email", "").strip()

            if not employee_id:
                error = "Please enter your Employee ID."
            elif not current_password:
                error = "Your current password is required to replace your authenticator."
            else:
                try:
                    success = request_authenticator_replacement(
                        employee_id=employee_id,
                        current_password=current_password,
                        email=comp_email,
                        base_url=base_url,
                    )
                    if success:
                        success_msg = (
                            "If your password and company email are verified, "
                            "a single-use authenticator replacement link has been sent to your email (valid for 30 seconds)."
                        )
                    else:
                        error = "Authentication failed or account does not meet self-service replacement criteria."
                except (ValidationError, AuthException, PermissionDenied) as e:
                    error = e.messages[0] if hasattr(e, "messages") else str(e)

        # -------------------------------------------------------------------
        # Direct / Legacy Code Redemption (action="redeem_code")
        # -------------------------------------------------------------------
        elif action == "redeem_code":
            code_type = request.POST.get("code_type", "").strip()
            code_val = request.POST.get("code_value", "").strip()
            if code_type == "mfa_reset_token":
                current_pw = request.POST.get("current_password", "")
                try:
                    user = verify_mfa_reset_and_authorize(
                        employee_id=employee_id,
                        raw_code=code_val,
                        password=current_pw,
                    )
                    request.session["_salow_act_user_id"] = user.id
                    return redirect("accounts:activate_mfa_setup")
                except Exception:
                    error = "Invalid recovery submission. Please verify your credentials or contact an administrator."
            else:
                # recovery_code / totp direct password reset skipped the email step: not available.
                error = "This recovery action is not available. Use one of the three options on this page."

    return render(
        request,
        "accounts/recovery/forgot_password.html",
        {
            "error": error,
            "success_msg": success_msg,
            "employee_id": employee_id,
            "active_method": active_method,
            "sub_route": sub_route,
        },
    )


# ---------------------------------------------------------------------------
# Route C & Verified Proof: Password Reset Completion Screen
# ---------------------------------------------------------------------------


def email_reset_landing_view(request):
    """
    Email link continuation screen for password reset (Route C & Approved Admin Reset).
    GET: Validates token without consuming it. Email security scanners do not consume the token.
    POST: Consumes token atomically, exchanges it for short-lived session state, and redirects
          to password-set view, stripping the bearer token from the visible URL.
    """
    raw_token = request.GET.get("token") or request.POST.get("token") or ""

    if not raw_token:
        messages.error(request, "Invalid or missing password reset link.")
        return redirect("accounts:forgot_password")

    try:
        user = verify_email_reset_token(raw_token)
    except AuthException as e:
        messages.error(request, e.user_message)
        return redirect("accounts:forgot_password")

    if request.method == "POST":
        try:
            # Atomically consume token
            complete_user = consume_email_token(raw_token, ActivationCode.Purpose.PASSWORD_RESET)[1]
            request.session[PW_RESET_SESSION_UID] = complete_user.id
            request.session.set_expiry(900)
            return redirect("accounts:email_set_password")
        except AuthException as e:
            messages.error(request, e.user_message)
            return redirect("accounts:forgot_password")

    return render(
        request,
        "accounts/recovery/email_reset_continue.html",
        {
            "token": raw_token,
            "employee_id": user.employee_id,
        },
    )


def email_set_password_view(request):
    """
    Protected password reset form (Route A, Route B, Route C, Admin Approved).
    Only renders after successful verification of recovery code, TOTP, or email token.
    Bearer token is absent from the URL.
    """
    user_id = request.session.get(PW_RESET_SESSION_UID)
    if not user_id:
        messages.error(request, "Password reset session has expired. Please verify again.")
        return redirect("accounts:forgot_password")

    user = CustomUser.objects.filter(id=user_id, is_active=True).first()
    if not user:
        return redirect("accounts:forgot_password")

    error = None
    if request.method == "POST":
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if new_password != confirm_password:
            error = "Passwords do not match."
        else:
            try:
                validate_employee_password(new_password, user)
                with transaction.atomic():
                    set_user_password(user, new_password)
                    revoke_user_sessions(user)
                    ActivationCode.objects.filter(
                        user=user,
                        purpose=ActivationCode.Purpose.PASSWORD_RESET,
                        is_live=1,
                    ).update(is_live=None)

                # Clear session state
                request.session.pop(PW_RESET_SESSION_UID, None)
                messages.success(
                    request,
                    "Your password has been successfully updated. Please sign in with your new password.",
                )
                return redirect("accounts:login")
            except ValidationError as e:
                error = e.messages[0] if hasattr(e, "messages") else str(e)
            except AuthException as e:
                error = e.user_message

    return render(
        request,
        "accounts/recovery/set_new_password.html",
        {
            "error": error,
            "employee_id": user.employee_id,
        },
    )


# ---------------------------------------------------------------------------
# Method 3 — Authenticator Replacement Flow
# ---------------------------------------------------------------------------


def mfa_replacement_verify_view(request):
    """
    Continuation screen for lost/replaced authenticator email link.
    GET: Validates token without consuming it.
    POST: Consumes token atomically, authorizes replacement state, and redirects to setup.
    """
    raw_token = request.GET.get("token") or request.POST.get("token") or ""

    if not raw_token:
        messages.error(request, "Invalid or missing authenticator verification link.")
        return redirect("accounts:forgot_password")

    try:
        user = verify_mfa_replacement_token(raw_token)
    except AuthException as e:
        messages.error(request, e.user_message)
        return redirect("accounts:forgot_password")

    if request.method == "POST":
        try:
            authorized_user = consume_email_token(
                raw_token, ActivationCode.Purpose.MFA_REPLACEMENT
            )[1]
            request.session[MFA_REPLACE_SESSION_UID] = authorized_user.id
            request.session.set_expiry(900)
            return redirect("accounts:mfa_replacement_setup")
        except AuthException as e:
            messages.error(request, e.user_message)
            return redirect("accounts:forgot_password")

    return render(
        request,
        "accounts/recovery/mfa_replacement_continue.html",
        {
            "token": raw_token,
            "employee_id": user.employee_id,
        },
    )


def mfa_replacement_setup_view(request):
    """
    New Authenticator Setup screen for replacement flow.
    Displays SVG QR code and grouped manual secret.
    Requires code verification before replacing the previous authenticator.
    """
    user_id = request.session.get(MFA_REPLACE_SESSION_UID)
    if not user_id:
        messages.error(request, "Authenticator replacement session expired.")
        return redirect("accounts:forgot_password")

    user = CustomUser.objects.filter(id=user_id, is_active=True).first()
    if not user:
        return redirect("accounts:forgot_password")

    secret, uri, _ = get_or_create_pending_totp(user)
    qr_svg = generate_qr_svg(uri)

    grouped_secret = " ".join([secret[i : i + 4] for i in range(0, len(secret), 4)])

    error = None
    if request.method == "POST":
        totp_code = request.POST.get("code", "").strip()
        if not totp_code:
            error = "Please enter the 6-digit code from your authenticator app."
        else:
            try:
                new_codes = complete_authenticator_replacement(user, totp_code)
                request.session[MFA_REPLACE_CODES_KEY] = new_codes
                return redirect("accounts:mfa_replacement_recovery_codes")
            except AuthException as e:
                error = e.user_message

    return render(
        request,
        "accounts/recovery/mfa_replacement_setup.html",
        {
            "qr_svg": qr_svg,
            "secret": grouped_secret,
            "error": error,
            "employee_id": user.employee_id,
        },
    )


def mfa_replacement_recovery_codes_view(request):
    """
    Displays the 10 fresh recovery codes once following authenticator replacement.
    Requires explicit checkbox acknowledgement. On completion, establishes session
    and redirects directly to the authorized role dashboard.
    """
    user_id = request.session.get(MFA_REPLACE_SESSION_UID)
    recovery_codes = request.session.get(MFA_REPLACE_CODES_KEY)

    if not user_id or not recovery_codes:
        messages.error(request, "Recovery code display session expired.")
        return redirect("accounts:login")

    user = CustomUser.objects.filter(id=user_id, is_active=True).first()
    if not user:
        return redirect("accounts:login")

    error = None
    if request.method == "POST":
        confirmed = request.POST.get("confirmed_saved") == "1"
        if not confirmed:
            error = "You must confirm that you have saved your recovery codes before proceeding."
        else:
            # Clean up replacement session keys
            request.session.pop(MFA_REPLACE_SESSION_UID, None)
            request.session.pop(MFA_REPLACE_CODES_KEY, None)

            # Establish server-side authenticated session
            create_verified_session(request, user)

            messages.success(
                request,
                "Your authenticator app and recovery codes have been successfully updated.",
            )

            # Direct to role-based dashboard
            if user.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
                return redirect("attendance:panel")
            elif user.role == CustomUser.Role.HR:
                return redirect("directory:panel")
            else:
                return redirect("attendance:panel")

    formatted_codes_text = "\n".join(recovery_codes)
    return render(
        request,
        "accounts/recovery/mfa_replacement_codes.html",
        {
            "codes": recovery_codes,
            "formatted_codes_text": formatted_codes_text,
            "error": error,
            "employee_id": user.employee_id,
        },
    )


# ---------------------------------------------------------------------------
# Company Email Ownership Verification View
# ---------------------------------------------------------------------------


def verify_email_view(request):
    """
    Validates company email ownership link.
    GET: Safe continuation screen.
    POST: Consumes token atomically, marks user.email_verified = True.
    """
    raw_token = request.GET.get("token") or request.POST.get("token") or ""

    if not raw_token:
        messages.error(request, "Invalid or missing email verification link.")
        return redirect("accounts:login")

    try:
        user = verify_email_token_safe(raw_token, ActivationCode.Purpose.EMAIL_VERIFICATION)[1]
    except AuthException as e:
        messages.error(request, e.user_message)
        return redirect("accounts:login")

    if request.method == "POST":
        try:
            verified_user = consume_email_token(
                raw_token, ActivationCode.Purpose.EMAIL_VERIFICATION
            )[1]
            verified_user.email_verified = True
            verified_user.save(update_fields=["email_verified", "updated_at"])

            log_auth_event(
                event="email_verified_successfully",
                code="INFO",
                step="verify_email",
                employee_id=verified_user.employee_id,
                message=f"Company email {verified_user.email} verified successfully",
            )
            messages.success(
                request,
                f"Your company email ({verified_user.email}) has been verified successfully.",
            )
            return redirect("accounts:login")
        except AuthException as e:
            messages.error(request, e.user_message)
            return redirect("accounts:login")

    return render(
        request,
        "accounts/recovery/email_verification_continue.html",
        {
            "token": raw_token,
            "employee_id": user.employee_id,
            "email": user.email,
        },
    )


# ---------------------------------------------------------------------------
# Salow Admin IAM Interface & Recovery Queue Console
# ---------------------------------------------------------------------------


@login_required
def admin_recovery_console_view(request):  # noqa: C901
    """
    Dedicated recovery management queue for authorized administrators.
    Displays pending employee recovery requests and approval actions.
    Enforces sudo verification and dual-control approvals.
    """
    actor: CustomUser = request.user
    if actor.role == CustomUser.Role.INTERN:
        return redirect("attendance:panel")

    if not (
        has_permission(actor, PERM_RECOVERY_DELEGATED) or has_permission(actor, PERM_RECOVERY_ADMIN)
    ):
        raise PermissionDenied("You do not have permission to access the IAM Recovery Console.")

    # If POST with target_employee_id (direct issuance request)
    if request.method == "POST":
        target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
        if target_emp_id:
            # Enforce sudo mode before generation
            if not is_sudo_active(request):
                next_url = reverse("accounts:admin_recovery_console")
                return redirect(f"{reverse('accounts:sudo')}?next={next_url}")

            recovery_type = request.POST.get("recovery_type", "password_reset")
            target_user = CustomUser.objects.filter(
                employee_id=target_emp_id, is_active=True
            ).first()
            if not target_user:
                messages.error(request, f"User {target_emp_id} not found.")
                return redirect("accounts:admin_recovery_console")

            from apps.accounts.auth.reset import check_recovery_authority

            check_recovery_authority(actor=actor, target_user=target_user)

            if recovery_type == "mfa_reset":
                from apps.accounts.auth.reset import issue_mfa_reset_code

                _, raw_code = issue_mfa_reset_code(target_user=target_user, issuing_admin=actor)
                title = "Single-Use MFA Reset Token Generated"
            else:
                from apps.accounts.auth.activation import issue_activation_code
                from apps.accounts.models import ActivationCode

                _, raw_code = issue_activation_code(
                    user=target_user,
                    purpose=ActivationCode.Purpose.PASSWORD_RESET,
                    issued_by=actor,
                )
                title = "Single-Use Password Reset Token Generated"

            return render(
                request,
                "accounts/recovery/admin_console.html",
                {
                    "actor": actor,
                    "target_user": target_user,
                    "generated_token": raw_code,
                    "token_title": title,
                    "pending_requests": RecoveryRequest.objects.filter(
                        status=RecoveryRequest.Status.PENDING
                    ).select_related("user"),
                    "super_admins": CustomUser.objects.filter(
                        role=CustomUser.Role.SUPER_ADMIN, is_active=True
                    ).exclude(id=actor.id),
                },
            )

    # Sudo mode enforcement for viewing console
    if not is_sudo_active(request):
        next_url = reverse("accounts:admin_recovery_console")
        return redirect(f"{reverse('accounts:sudo')}?next={next_url}")

    pending_requests = RecoveryRequest.objects.filter(
        status=RecoveryRequest.Status.PENDING
    ).select_related("user")

    super_admins = CustomUser.objects.filter(
        role=CustomUser.Role.SUPER_ADMIN, is_active=True
    ).exclude(id=actor.id)

    return render(
        request,
        "accounts/recovery/admin_console.html",
        {
            "actor": actor,
            "pending_requests": pending_requests,
            "super_admins": super_admins,
        },
    )


@login_required
@require_POST
def admin_approve_recovery_view(request, request_id):
    """Admin action to approve a pending recovery request."""
    actor: CustomUser = request.user
    if not (
        has_permission(actor, PERM_RECOVERY_DELEGATED) or has_permission(actor, PERM_RECOVERY_ADMIN)
    ):
        raise PermissionDenied("Unauthorized.")

    second_approver_id = request.POST.get("second_approver_id")
    second_approver = None
    if second_approver_id:
        second_approver = CustomUser.objects.filter(id=second_approver_id, is_active=True).first()

    base_url = request.build_absolute_uri("/")[:-1]

    try:
        req, reset_token = approve_administrator_reset(
            request_id=request_id,
            actor=actor,
            second_approver=second_approver,
            base_url=base_url,
        )
        messages.success(
            request,
            f"Recovery request for {req.user.employee_id} approved. Reset link dispatched.",
        )
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))

    return redirect("accounts:admin_recovery_console")


@login_required
@require_POST
def admin_reject_recovery_view(request, request_id):
    """Admin action to reject a pending recovery request."""
    actor: CustomUser = request.user
    if not (
        has_permission(actor, PERM_RECOVERY_DELEGATED) or has_permission(actor, PERM_RECOVERY_ADMIN)
    ):
        raise PermissionDenied("Unauthorized.")

    reason = request.POST.get("reason", "Verification requirements not met.")

    try:
        req = reject_administrator_reset(
            request_id=request_id,
            actor=actor,
            reason=reason,
        )
        messages.info(request, f"Recovery request for {req.user.employee_id} rejected.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))

    return redirect("accounts:admin_recovery_console")


@require_POST
def passkey_recovery_verify_view(request):
    """
    Accepts WebAuthn assertion payload and resets employee password,
    revoking all existing device sessions.
    """
    try:
        data = json.loads(request.body.decode("utf-8")) if request.body else {}
        credential_data = data.get("credential", {})
        employee_id = data.get("employee_id", "").strip().upper()
        new_password = data.get("new_password", "")

        user = verify_passkey_authentication(
            request=request,
            credential_data=credential_data,
            employee_id=employee_id,
        )
        if not user:
            return JsonResponse(
                {"success": False, "error": "Passkey verification failed."},
                status=400,
            )

        validate_employee_password(new_password, user)

        with transaction.atomic():
            set_user_password(user, new_password)
            revoke_user_sessions(user)
            log_auth_event(
                event="passkey_recovery_completed",
                code="INFO",
                step="passkey_recovery",
                employee_id=user.employee_id,
                message="Password reset successfully using passkey assertion; all sessions revoked",
            )

        return JsonResponse({"success": True, "redirect_url": reverse("accounts:login")})
    except (AuthException, ValidationError) as e:
        return JsonResponse(
            {"success": False, "error": getattr(e, "user_message", str(e))},
            status=400,
        )
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=400)
