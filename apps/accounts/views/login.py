"""
Everyday Login & MFA Challenge Views.
Mandatory flow:
/login -> /mfa/challenge -> workspace dashboard.
STRICT REQUIREMENT: The MFA challenge page must NOT display a QR code.
"""

import time

from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.login_flow import (
    authenticate_password,
)
from apps.accounts.auth.sessions import revoke_user_sessions
from apps.accounts.models import CustomUser, TOTPCredential

PENDING_LOGIN_USER_ID_KEY = "_salow_login_pending_uid"
PENDING_LOGIN_TS_KEY = "_salow_login_pending_ts"
PENDING_LOGIN_ATTEMPTS_KEY = "_salow_login_pending_attempts"
LOGIN_NEXT_URL_KEY = "_salow_login_next_url"
PREAUTH_TIMEOUT_SECONDS = 300  # 5 minutes
PREAUTH_MAX_ATTEMPTS = 5


def login_view(request):
    """
    Everyday login handler supporting:
    - Flow B: Single-screen returning employee login (Employee ID + Password + TOTP/Recovery code -> Confirm).
    - Step-by-step fallback (/login -> /mfa/challenge/).
    - Progressive disclosure: initial state displays only the two action buttons (Login & Sign In).
    """
    if request.user.is_authenticated:
        return redirect("attendance:panel")

    redirect_to = request.POST.get("next") or request.GET.get("next") or ""
    flow = request.GET.get("flow", "")
    error = None

    if request.method == "POST":
        flow = "signin"
        emp_id = request.POST.get("employee_id", "").strip().upper()
        password = request.POST.get("password", "")
        totp_code = (
            request.POST.get("totp_code", "").strip() or request.POST.get("token", "").strip()
        )
        recovery_code = request.POST.get("recovery_code", "").strip()
        ip_addr = getattr(request, "client_ip", None) or request.META.get("REMOTE_ADDR")

        from apps.accounts.auth.authentication_centre import (
            complete_auth_transaction,
            record_transaction_stage,
            start_auth_transaction,
        )
        tx = start_auth_transaction(
            domain="workspace",
            identity_ref=emp_id,
            client_ip=ip_addr,
        )

        try:
            user = authenticate_password(emp_id, password, ip_address=ip_addr)
            record_transaction_stage(tx.transaction_id, "password_check", "PASS", "Password verified", duration_ms=12.0)
            record_transaction_stage(tx.transaction_id, "lifecycle", "PASS", f"User status: {user.status}")

            from apps.accounts.auth.risk_engine import decide_login_risk, RiskDecision
            risk_decision, risk_score, risk_signals = decide_login_risk(
                user, ip_addr or "127.0.0.1", request.META.get("HTTP_USER_AGENT", "")
            )
            decision_val = getattr(risk_decision, "value", str(risk_decision))
            record_transaction_stage(
                tx.transaction_id,
                "risk_evaluation",
                decision_val.upper(),
                f"Risk score: {risk_score}",
                risk_decision=decision_val,
                risk_signals=risk_signals,
            )

            if risk_decision == RiskDecision.CONTAIN:
                user.status = CustomUser.Status.LOCKED
                user.save(update_fields=["status"])
                complete_auth_transaction(tx.transaction_id, "denied", reason_category="risk_contain", risk_decision=decision_val, risk_signals=risk_signals)
                raise AuthException(
                    "AUTH-SEC-002",
                    step="login_risk",
                    message_override="Account security hold applied due to suspicious activity. Please contact IT Security.",
                )
            elif risk_decision == RiskDecision.DENY:
                complete_auth_transaction(tx.transaction_id, "denied", reason_category="risk_deny", risk_decision=decision_val, risk_signals=risk_signals)
                raise AuthException(
                    "AUTH-SEC-001",
                    step="login_risk",
                    message_override="Sign-in request blocked by security policy. Please try again later.",
                )

            # Check if user has completed MFA setup
            cred = TOTPCredential.objects.filter(user=user, confirmed_at__isnull=False).first()
            if not cred:
                # First time login without MFA: route to MFA enrollment
                request.session["_salow_act_user_id"] = user.id
                record_transaction_stage(tx.transaction_id, "mfa_challenge", "PENDING", "MFA enrollment required")
                complete_auth_transaction(tx.transaction_id, "challenged", reason_category="mfa_enrollment_required")
                return redirect("accounts:activate_mfa_setup")

            # Single-screen returning user authentication (Flow B)
            if totp_code or recovery_code:
                code_to_verify = recovery_code or totp_code
                clean_code = code_to_verify.replace("-", "").strip()
                is_recovery = bool(recovery_code) or (
                    len(clean_code) != 6 or not clean_code.isdigit()
                )

                # Check if this user already has an active session on another device (Section 9)
                from apps.accounts.auth.device_approval import (
                    create_device_approval_request,
                    is_second_device_request,
                )
                from apps.accounts.auth.mfa_totp import verify_totp_code
                from apps.accounts.auth.recovery_codes import redeem_recovery_code
                from apps.accounts.auth.sessions import create_verified_session
                from apps.accounts.auth.lockout import clear_failed_attempts

                if is_recovery:
                    redeem_recovery_code(user, clean_code)
                    record_transaction_stage(tx.transaction_id, "mfa_verification", "PASS", "Recovery code redeemed")
                else:
                    verify_totp_code(user, clean_code)
                    record_transaction_stage(tx.transaction_id, "mfa_verification", "PASS", "TOTP code verified")

                if is_second_device_request(
                    user,
                    ip_address=ip_addr,
                    user_agent=request.META.get("HTTP_USER_AGENT"),
                ):
                    req_dev = create_device_approval_request(
                        user=user,
                        ip_address=ip_addr,
                        user_agent=request.META.get("HTTP_USER_AGENT"),
                    )
                    record_transaction_stage(tx.transaction_id, "device_check", "CHALLENGE", "Second device requires approval", device_ref=str(req_dev.request_id))
                    complete_auth_transaction(tx.transaction_id, "challenged", reason_category="device_approval_required")
                    return redirect("accounts:device_approval_wait", request_id=req_dev.request_id)

                record_transaction_stage(tx.transaction_id, "device_check", "PASS", "Device approved")
                record_transaction_stage(tx.transaction_id, "session_decision", "PASS", "Session issued")
                create_verified_session(request, user)
                clear_failed_attempts(f"emp:{user.employee_id}")

                complete_auth_transaction(
                    tx.transaction_id,
                    "success",
                    reason_category="credentials_and_mfa_valid",
                    session_ref=request.session.session_key or "",
                    risk_decision=risk_decision.value,
                    risk_signals=risk_signals,
                )

                if redirect_to and url_has_allowed_host_and_scheme(
                    url=redirect_to,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(redirect_to)
                return redirect("attendance:panel")

            # Otherwise, 2-step flow (redirect to mfa_challenge)
            request.session[PENDING_LOGIN_USER_ID_KEY] = user.id
            request.session[PENDING_LOGIN_TS_KEY] = time.time()
            request.session[PENDING_LOGIN_ATTEMPTS_KEY] = 0
            request.session[LOGIN_NEXT_URL_KEY] = redirect_to
            request.session["_salow_login_tx_id"] = tx.transaction_id
            record_transaction_stage(tx.transaction_id, "mfa_challenge", "PENDING", "Awaiting second factor challenge")
            return redirect("accounts:mfa_challenge")
        except AuthException as e:
            error = e.user_message
            record_transaction_stage(tx.transaction_id, "password_check", "FAIL", str(e))
            complete_auth_transaction(tx.transaction_id, "failed", reason_category="auth_exception")
            try:
                from apps.accounts.auth.spraying_detection import record_auth_failure_and_check_spray
                record_auth_failure_and_check_spray(ip_addr or "127.0.0.1", emp_id)
            except Exception:
                pass

    return render(
        request,
        "accounts/login/login.html",
        {"error": error, "next": redirect_to, "flow": flow},
    )


def mfa_challenge_view(request):  # noqa: C901
    """
    Step 2 of everyday login: 6-digit authenticator challenge or recovery code.
    Strictly NO QR CODE is rendered on this page.
    Enforces pre-authentication timeout (5m) and max failed attempt threshold.
    """
    pending_uid = request.session.get(PENDING_LOGIN_USER_ID_KEY)
    pending_ts = request.session.get(PENDING_LOGIN_TS_KEY)
    attempts = request.session.get(PENDING_LOGIN_ATTEMPTS_KEY, 0)

    if not pending_uid:
        return redirect("accounts:login")

    # Enforce pre-auth timeout
    if pending_ts and (time.time() - pending_ts > PREAUTH_TIMEOUT_SECONDS):
        for key in (
            PENDING_LOGIN_USER_ID_KEY,
            PENDING_LOGIN_TS_KEY,
            PENDING_LOGIN_ATTEMPTS_KEY,
            LOGIN_NEXT_URL_KEY,
        ):
            request.session.pop(key, None)
        messages.error(request, "Your login verification session expired. Please sign in again.")
        return redirect("accounts:login")

    # Enforce max pre-auth attempts
    if attempts >= PREAUTH_MAX_ATTEMPTS:
        for key in (
            PENDING_LOGIN_USER_ID_KEY,
            PENDING_LOGIN_TS_KEY,
            PENDING_LOGIN_ATTEMPTS_KEY,
            LOGIN_NEXT_URL_KEY,
        ):
            request.session.pop(key, None)
        messages.error(request, "Too many failed verification attempts. Please sign in again.")
        return redirect("accounts:login")

    user = CustomUser.objects.filter(id=pending_uid, is_active=True).first()
    if not user:
        for key in (
            PENDING_LOGIN_USER_ID_KEY,
            PENDING_LOGIN_TS_KEY,
            PENDING_LOGIN_ATTEMPTS_KEY,
            LOGIN_NEXT_URL_KEY,
        ):
            request.session.pop(key, None)
        return redirect("accounts:login")

    error = None
    if request.method == "POST":
        totp_code = request.POST.get("token", "").strip()
        recovery_code = request.POST.get("recovery_code", "").strip()

        try:
            from apps.accounts.auth.device_approval import (
                create_device_approval_request,
                is_second_device_request,
            )
            from apps.accounts.auth.mfa_totp import verify_totp_code
            from apps.accounts.auth.recovery_codes import redeem_recovery_code
            from apps.accounts.auth.sessions import create_verified_session
            from apps.accounts.auth.lockout import clear_failed_attempts

            if recovery_code:
                redeem_recovery_code(user, recovery_code.strip())
            elif totp_code:
                verify_totp_code(user, totp_code.strip())
            else:
                error = (
                    "Please enter either your 6-digit authenticator code or a backup recovery code."
                )

            if not error:
                client_ip = getattr(request, "client_ip", None) or request.META.get("REMOTE_ADDR")
                user_agent = request.META.get("HTTP_USER_AGENT")
                if is_second_device_request(
                    user,
                    ip_address=client_ip,
                    user_agent=user_agent,
                ):
                    req_dev = create_device_approval_request(
                        user=user,
                        ip_address=client_ip,
                        user_agent=user_agent,
                    )
                    return redirect("accounts:device_approval_wait", request_id=req_dev.request_id)

                create_verified_session(request, user)
                clear_failed_attempts(f"emp:{user.employee_id}")

                tx_id = request.session.pop("_salow_login_tx_id", None)
                if tx_id:
                    from apps.accounts.auth.authentication_centre import complete_auth_transaction, record_transaction_stage
                    record_transaction_stage(tx_id, "mfa_verification", "PASS", "Second factor verified successfully")
                    record_transaction_stage(tx_id, "session_decision", "PASS", "Session issued")
                    complete_auth_transaction(tx_id, "success", reason_category="mfa_verified", session_ref=request.session.session_key or "")

                next_url = request.session.pop(LOGIN_NEXT_URL_KEY, "")
                for key in (
                    PENDING_LOGIN_USER_ID_KEY,
                    PENDING_LOGIN_TS_KEY,
                    PENDING_LOGIN_ATTEMPTS_KEY,
                ):
                    request.session.pop(key, None)
                if next_url and url_has_allowed_host_and_scheme(
                    url=next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(next_url)
                return redirect("attendance:panel")
        except AuthException as e:
            request.session[PENDING_LOGIN_ATTEMPTS_KEY] = attempts + 1
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
