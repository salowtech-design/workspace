"""
User Security Dashboard & Admin Console Views.
Provides active session management, revocation, and diagnostic audit search.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.accounts.auth.sessions import end_session_by_key
from apps.accounts.models import AuthEvent, CustomUser, UserSession


@login_required
def security_dashboard_view(request):
    """View active sessions and personal security status."""
    active_sessions = UserSession.objects.filter(user=request.user).order_by("-last_activity")
    recent_events = AuthEvent.objects.filter(employee_id=request.user.employee_id).order_by(
        "-time"
    )[:10]

    return render(
        request,
        "accounts/security/dashboard.html",
        {
            "sessions": active_sessions,
            "current_session_key": request.session.session_key,
            "recent_events": recent_events,
        },
    )


@login_required
def terminate_session_view(request, session_key: str):
    """End a specific active session."""
    if request.method == "POST":
        end_session_by_key(request.user, session_key)
        messages.success(request, "Session successfully terminated.")
    return redirect("accounts:security")


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
