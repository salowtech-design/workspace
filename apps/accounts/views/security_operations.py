"""
Security Operations (SecOps) Views — Salow Workspace IAM.
Implements:
1. `GET /security/events?after=<id>` with stable event cursor and HTMX polling (`every 15s`).
2. High-severity alerts triage dashboard.
3. Alert acknowledgement with mandatory justification and independent reviewer enforcement.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.auth.security_ops import (
    acknowledge_security_alert,
    get_security_events_stream,
)
from apps.accounts.models import AuthEvent, CustomUser, SecurityAlert
from apps.accounts.permissions import (
    PERM_SECURITY_ALERTS_ACK,
    PERM_SECURITY_EVENTS_READ,
    has_permission,
)


@login_required
def security_events_view(request):
    """
    Append-only security event endpoint.
    Path: GET /security/events?after=<id>
    Supports HTMX polling (`hx-trigger="every 15s"`) or JSON API.
    Zero secret disclosure.
    """
    actor: CustomUser = request.user
    if not has_permission(actor, PERM_SECURITY_EVENTS_READ) and actor.role not in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SECURITY_REVIEWER,
    ):
        raise PermissionDenied("Access restricted to Security Administrators and Reviewers.")

    after_raw = request.GET.get("after")
    after_id = None
    if after_raw and after_raw.isdigit():
        after_id = int(after_raw)

    limit = min(int(request.GET.get("limit", 50)), 100)
    events = get_security_events_stream(after_id=after_id, limit=limit)

    # If requested as JSON
    if request.headers.get("accept") == "application/json" or request.GET.get("format") == "json":
        return JsonResponse({"events": events, "count": len(events), "after": after_id})

    # Render HTMX HTML fragment
    return render(
        request,
        "accounts/security/htmx_events_feed.html",
        {"events": events, "latest_id": events[-1]["id"] if events else after_id},
    )


@login_required
def security_alerts_view(request):
    """Security alert triage console."""
    actor: CustomUser = request.user
    if not has_permission(actor, PERM_SECURITY_EVENTS_READ) and actor.role not in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SECURITY_REVIEWER,
    ):
        raise PermissionDenied("Access restricted to Security Administrators and Reviewers.")

    status_filter = request.GET.get("status", "open")
    alerts_qs = SecurityAlert.objects.all().order_by("-created_at")
    if status_filter:
        alerts_qs = alerts_qs.filter(status=status_filter)

    can_acknowledge = has_permission(actor, PERM_SECURITY_ALERTS_ACK) or actor.role in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.SECURITY_ADMIN,
    )

    return render(
        request,
        "accounts/security/alerts_triage.html",
        {
            "alerts": alerts_qs[:50],
            "status_filter": status_filter,
            "can_acknowledge": can_acknowledge,
        },
    )


@login_required
@require_POST
def security_alert_acknowledge_view(request, alert_id: int):
    """
    Acknowledge a high-severity security alert.
    Requires justification reason; strictly enforces independent review invariant.
    """
    reason = request.POST.get("reason", "").strip()
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/auth/admin/iam/?tab=investigation"

    try:
        acknowledge_security_alert(alert_id, request.user, reason)
        messages.success(request, f"Security alert #{alert_id} successfully acknowledged.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))

    return redirect(next_url)
