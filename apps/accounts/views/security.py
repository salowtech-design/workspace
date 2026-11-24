"""
User Security Dashboard & Admin Console Views.
Provides:
1. Active session management and "terminate all other sessions".
2. Passkey credential management.
3. Self-service password changes with current-password verification.
4. Idle timeout lock and unlock re-authentication.
5. Server-side draft persistence and workspace resumption.
6. Administrative audit inspection console.
"""

import json

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.sessions import (
    end_session_by_key,
    hash_session_bearer,
    revoke_user_sessions,
)
from apps.accounts.auth.stepup import verify_stepup
from apps.accounts.models import (
    AuthEvent,
    CustomUser,
    PasskeyCredential,
    UserSession,
    WorkspaceDraft,
)
from apps.accounts.services import change_user_password


@login_required
def security_dashboard_view(request):
    """View active sessions, registered passkeys, and personal security audit trail."""
    active_sessions = UserSession.objects.filter(user=request.user).order_by("-last_activity")
    passkeys = PasskeyCredential.objects.filter(user=request.user, is_active=True).order_by(
        "-created_at"
    )
    recent_events = AuthEvent.objects.filter(employee_id=request.user.employee_id).order_by(
        "-time"
    )[:15]

    return render(
        request,
        "accounts/security/dashboard.html",
        {
            "sessions": active_sessions,
            "passkeys": passkeys,
            "current_session_key": hash_session_bearer(request.session.session_key or ""),
            "recent_events": recent_events,
        },
    )


@login_required
@require_POST
def terminate_session_view(request, session_key: str):
    """End a specific active session."""
    end_session_by_key(request.user, session_key)
    messages.success(request, "Session successfully terminated.")
    return redirect("accounts:security")


@login_required
@require_POST
def terminate_all_other_sessions_view(request):
    """Revoke all active sessions except the current device session."""
    current_key = request.session.session_key
    count = revoke_user_sessions(request.user, keep_session_key=current_key)
    messages.success(request, f"Terminated {count} other active device sessions.")
    return redirect("accounts:security")


@login_required
def change_password_view(request):
    """
    Self-service password update.
    Requires current password verification, enforces strong password policy,
    and revokes all other active sessions.
    """
    error = None
    if request.method == "POST":
        current_password = request.POST.get("current_password", "")
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if new_password != confirm_password:
            error = "New passwords do not match."
        else:
            try:
                change_user_password(
                    user=request.user,
                    current_password=current_password,
                    new_password=new_password,
                    keep_session_key=request.session.session_key,
                )
                # The hash changed: re-bind this session to it, otherwise Django ends the very
                # session the user is working in while the page promises to keep it.
                update_session_auth_hash(request, request.user)
                messages.success(
                    request,
                    "Your password was changed successfully. All other active sessions have been revoked.",
                )
                return redirect("accounts:security")
            except ValidationError as e:
                error = e.messages[0] if hasattr(e, "messages") else str(e)
            except Exception as e:
                error = f"Failed to change password: {e}"

    return render(
        request,
        "accounts/security/change_password.html",
        {"error": error},
    )


@login_required
def idle_lock_view(request):
    """Render workspace idle lock screen when user has been idle for 15 minutes."""
    request.session["_salow_workspace_locked"] = True
    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or "/attendance/"
    return render(
        request,
        "accounts/security/idle_lock.html",
        {"user": request.user, "next": next_url},
    )


@login_required
@require_POST
def idle_unlock_view(request):
    """Unlock workspace after user re-enters password without invalidating work."""
    password = request.POST.get("password", "")
    next_url = request.POST.get("next", "/attendance/")

    if not request.user.check_password(password):
        return render(
            request,
            "accounts/security/idle_lock.html",
            {
                "user": request.user,
                "next": next_url,
                "error": "Incorrect password entered.",
            },
            status=401,
        )

    request.session.pop("_salow_workspace_locked", None)
    log_auth_event(
        event="workspace_unlocked",
        code="INFO",
        step="idle_unlock",
        employee_id=request.user.employee_id,
        message="Workspace unlocked after idle timeout",
    )
    return redirect(next_url)


@login_required
@require_POST
def save_draft_view(request):
    """Server-side draft persistence for work-in-progress across lock/unlock."""
    try:
        body = json.loads(request.body.decode("utf-8"))
        path = body.get("path", "").strip()
        data = body.get("draft_data", {})
        if not path:
            return JsonResponse({"error": "Path required"}, status=400)

        WorkspaceDraft.objects.update_or_create(
            user=request.user,
            path=path,
            defaults={"draft_data": data},
        )
        return JsonResponse({"status": "saved"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
def get_draft_view(request):
    """Retrieve saved server-side draft for a given workspace path."""
    path = request.GET.get("path", "").strip()
    if not path:
        return JsonResponse({"error": "Path required"}, status=400)

    draft = WorkspaceDraft.objects.filter(user=request.user, path=path).first()
    if draft:
        return JsonResponse({"status": "found", "draft_data": draft.draft_data})
    return JsonResponse({"status": "not_found", "draft_data": {}})


@login_required
def console_auth_events_view(request):
    """Administrative inspection of authentication audit timeline."""
    if request.user.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        messages.error(request, "Access restricted to administrators.")
        return redirect("attendance:panel")

    code_filter = request.GET.get("code", "").strip()
    emp_filter = request.GET.get("emp", "").strip()

    qs = AuthEvent.objects.all().order_by("-time")
    if code_filter:
        qs = qs.filter(code__icontains=code_filter)
    if emp_filter:
        qs = qs.filter(employee_id__icontains=emp_filter)

    events = qs[:100]
    return render(
        request,
        "accounts/security/console_events.html",
        {"events": events, "code_filter": code_filter, "emp_filter": emp_filter},
    )


@login_required
def sudo_elevation_view(request):
    """
    Sudo mode / step-up re-authentication view.
    Prompts authenticated user for password (and TOTP code if Super Admin) to unlock
    sensitive operations for the duration of the sudo window (10 minutes).
    """
    next_url = request.GET.get("next") or request.POST.get("next") or "/auth/security/"
    error = None

    if request.method == "POST":
        password = request.POST.get("password", "")
        totp_code = request.POST.get("totp_code", "").strip() or None

        try:
            verify_stepup(request, password=password, totp_code=totp_code)
            messages.success(request, "Step-up authentication verified.")
            if url_has_allowed_host_and_scheme(
                url=next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect("accounts:security")
        except AuthException as e:
            error = e.user_message
        except Exception as e:
            error = f"Verification failed: {e}"

    return render(
        request,
        "accounts/security/sudo.html",
        {
            "next": next_url,
            "error": error,
            "is_super_admin": request.user.role == CustomUser.Role.SUPER_ADMIN,
        },
    )


@login_required
def session_status_view(request):
    """Passive status for the idle warning banner. Does not count as activity."""
    from apps.accounts.auth import workspace_idle

    limit = workspace_idle.idle_limit_seconds()
    return JsonResponse(
        {
            "remaining_seconds": max(0, workspace_idle.seconds_remaining(request)),
            "limit_seconds": limit,
            "warning_seconds": workspace_idle.warning_seconds(limit),
        }
    )


@login_required
@require_POST
def session_keepalive_view(request):
    """Explicit 'stay signed in' or user input: resets the idle clock."""
    from apps.accounts.auth import workspace_idle

    workspace_idle.touch(request)
    return JsonResponse({"remaining_seconds": workspace_idle.seconds_remaining(request)})
