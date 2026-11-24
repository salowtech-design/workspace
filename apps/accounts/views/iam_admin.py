"""
Dedicated IAM Administrator Views for iam.salowtech.com.
Provides:
1. Dedicated IAM Administrator Login (ID + min 16-char password + fresh TOTP)
2. IAM Administrator Logout
3. Step-Up Re-Authentication (Level 2 Assurance)
4. Central IAM Dashboard with Assurance Level Enforcement
5. Dedicated Workspace Security Administration Tab (Section 11)
"""

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.iam_auth import (
    IAM_SESSION_COOKIE_NAME,
    authenticate_iam_admin,
    check_iam_assurance,
    create_iam_session,
    get_iam_session_from_request,
    stepup_iam_assurance,
)
from apps.accounts.models import WorkspaceSecurityPolicy


def iam_login_view(request):
    """
    Dedicated IAM Administrator Sign-In (Section 4.1):
    1. Dedicated IAM administrator ID.
    2. Dedicated IAM password (min 16 chars).
    3. Fresh IAM TOTP.
    4. Server-side account and risk checks.
    5. Level 1 assurance.
    6. Sets __Host-iam_session cookie.
    """
    iam_session = get_iam_session_from_request(request)
    if iam_session:
        return redirect("iam:dashboard")

    error = None
    if request.method == "POST":
        admin_id = request.POST.get("admin_id", "").strip().upper()
        password = request.POST.get("password", "")
        totp_code = request.POST.get("totp_code", "").strip()
        client_ip = getattr(request, "client_ip", None) or request.META.get(
            "REMOTE_ADDR", "127.0.0.1"
        )

        tx = None
        if admin_id:
            from apps.accounts.auth.authentication_centre import (
                complete_auth_transaction,
                record_transaction_stage,
                start_auth_transaction,
            )

            tx = start_auth_transaction(
                domain="iam",
                identity_ref=admin_id,
                client_ip=client_ip,
            )

        if not admin_id or not password or not totp_code:
            error = "IAM Administrator ID, Password, and TOTP code are required."
            if tx:
                record_transaction_stage(
                    tx.transaction_id, "password_check", "FAIL", "Missing fields"
                )
                complete_auth_transaction(
                    tx.transaction_id, "failed", reason_category="missing_fields"
                )
        elif len(password) < 16:
            error = "IAM Administrator password must meet the 16-character minimum requirement."
            if tx:
                record_transaction_stage(
                    tx.transaction_id, "password_check", "FAIL", "Password length below 16 chars"
                )
                complete_auth_transaction(
                    tx.transaction_id, "failed", reason_category="password_length_violation"
                )
        else:
            try:
                iam_admin = authenticate_iam_admin(
                    admin_id=admin_id,
                    password=password,
                    totp_code=totp_code,
                    client_ip=client_ip,
                )
                session = create_iam_session(iam_admin, request, assurance_level=1)
                if tx:
                    record_transaction_stage(
                        tx.transaction_id, "password_check", "PASS", "Password verified"
                    )
                    record_transaction_stage(
                        tx.transaction_id, "mfa_verification", "PASS", "TOTP valid"
                    )
                    record_transaction_stage(
                        tx.transaction_id, "lifecycle", "PASS", f"Status: {iam_admin.status}"
                    )
                    complete_auth_transaction(
                        tx.transaction_id,
                        "success",
                        reason_category="credentials_and_mfa_valid",
                        session_ref=session.session_key,
                    )

                next_url = request.GET.get("next") or reverse("iam:dashboard")

                response = redirect(next_url)
                # Set host-only IAM session cookie (__Host-salow_iam_session)
                cookie_name = IAM_SESSION_COOKIE_NAME
                secure = getattr(settings, "SESSION_COOKIE_SECURE", False)
                response.set_cookie(
                    cookie_name,
                    session.session_key,
                    httponly=True,
                    secure=secure,
                    samesite="Strict" if secure else "Lax",
                    path="/",
                )
                return response
            except AuthException as exc:
                error = exc.user_message
                if tx:
                    record_transaction_stage(tx.transaction_id, "password_check", "FAIL", str(exc))
                    complete_auth_transaction(
                        tx.transaction_id, "failed", reason_category="auth_exception"
                    )
            except Exception as exc:
                error = f"Authentication failed: {exc}"
                if tx:
                    record_transaction_stage(tx.transaction_id, "password_check", "FAIL", str(exc))
                    complete_auth_transaction(
                        tx.transaction_id, "failed", reason_category="unexpected_error"
                    )

    return render(
        request,
        "accounts/admin/iam_login.html",
        {"error": error},
    )


def iam_logout_view(request):
    """Logs out IAM administrator and terminates IAM session."""
    session = get_iam_session_from_request(request)
    if session:
        session.is_active = False
        session.save(update_fields=["is_active"])

    response = redirect("iam:login")
    response.delete_cookie(IAM_SESSION_COOKIE_NAME, path="/")
    return response


def iam_stepup_view(request):
    """
    Step-Up Re-Authentication for Sensitive Operations (Assurance Level 2).
    Requires IAM administrator password and fresh TOTP bound to exact action.
    """
    session = get_iam_session_from_request(request)
    if not session:
        return redirect("iam:login")

    action = request.GET.get("action") or request.POST.get("action") or "sensitive_operation"
    next_url = request.GET.get("next") or request.POST.get("next") or reverse("iam:dashboard")
    error = None

    if request.method == "POST":
        password = request.POST.get("password", "")
        totp_code = request.POST.get("totp_code", "").strip()

        if stepup_iam_assurance(session, password, totp_code, action_name=action):
            messages.success(
                request,
                f"Step-up authentication verified for '{action}' (Assurance Level 2 granted).",
            )
            return redirect(next_url)
        else:
            error = "Invalid password or TOTP code. Step-up authentication failed."

    return render(
        request,
        "accounts/admin/iam_stepup.html",
        {
            "action": action,
            "next_url": next_url,
            "iam_admin": session.iam_admin,
            "error": error,
        },
    )


def iam_dashboard_view(request):
    """
    Central IAM Console on iam.salowtech.com.
    Requires active IAM administrator session and displays the 13 operational subsystems
    plus the dedicated Workspace Security administration section (Section 11).
    """
    session = get_iam_session_from_request(request)
    if not session:
        login_url = reverse("iam:login")
        return redirect(f"{login_url}?next={request.path}")

    # Inject authenticated IAM context
    request.iam_admin = session.iam_admin
    request.iam_session = session
    # Provide backward-compatibility user representation for shared templates
    request.user = session.iam_admin.user

    active_tab = request.GET.get("tab") or request.POST.get("tab") or "identity"

    # Section 11: Dedicated Workspace Security Administration Tab inside IAM
    if active_tab == "workspace_security" and request.method == "POST":
        # Check Assurance Level 2 for policy changes
        if not check_iam_assurance(
            request, required_level=2, action_name="update_workspace_security_policy"
        ):
            stepup_url = reverse("iam:stepup")
            return redirect(
                f"{stepup_url}?action=update_workspace_security_policy&next={request.get_full_path()}"
            )

        policy_key = request.POST.get("policy_key", "").strip()
        policy_val_str = request.POST.get("policy_value", "{}").strip()
        justification = request.POST.get("justification", "").strip()

        if policy_key and justification:
            import json

            try:
                val = (
                    json.loads(policy_val_str)
                    if policy_val_str.startswith("{")
                    else {"value": policy_val_str}
                )
                WorkspaceSecurityPolicy.objects.update_or_create(
                    policy_key=policy_key,
                    defaults={
                        "policy_value": val,
                        "justification": justification,
                        "updated_by": session.iam_admin.user,
                    },
                )
                messages.success(
                    request, f"Workspace security policy '{policy_key}' updated successfully."
                )
            except Exception as e:
                messages.error(request, f"Failed to update policy: {e}")
        else:
            messages.error(request, "Policy key and justification are required.")

        return redirect(f"{request.path}?tab=workspace_security")

    # Delegate to the comprehensive 13-tile dashboard implementation
    from apps.accounts.views.admin_iam import admin_iam_dashboard_view

    return admin_iam_dashboard_view(request)
