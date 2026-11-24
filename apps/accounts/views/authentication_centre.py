"""
Salow Workspace Authentication Centre — Controller Views (Sequence 1 Implementation Contract).
Provides:
1. Authentication Centre Landing View (/console/authentication/) with exactly two distinct sub-options:
   - "Authentication Control" (Workspace employee authentication)
   - "IAM Authentication" (IAM administrator authentication)
2. Workspace Authentication Control Dashboard (/console/authentication/control/)
3. IAM Authentication Dashboard (/console/authentication/iam/)
4. HTMX 1-second Cursor-Based Live Activity Feed (/console/authentication/live-feed/)
5. IAM Administrator Presence Heartbeat Endpoint (/console/heartbeat/)
6. Searchable Records & Transaction Investigation Detail Views
7. Dispatcher for the 8 Real Control Actions (Session Revoke, Device Revoke, Temporary Restriction, etc.)
8. Dedicated IAM Sign In (/auth/sign-in/) and IAM Recovery Portal (/auth/recovery/)
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.auth.authentication_centre import (
    approve_iam_recovery_case,
    complete_auth_transaction,
    create_iam_recovery_case,
    execute_acknowledge_alert,
    execute_export_records,
    execute_release_lock,
    execute_revoke_device,
    execute_revoke_session,
    execute_start_investigation,
    execute_temporary_restriction,
    get_active_policy,
    get_all_admin_presences,
    get_auth_overview_metrics,
    get_live_activity_feed,
    probe_all_dependencies,
    record_admin_heartbeat,
    record_transaction_stage,
    reject_iam_recovery_case,
    save_policy_version,
    search_auth_records,
    start_auth_transaction,
)
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.iam_auth import (
    IAM_SESSION_COOKIE_NAME,
    authenticate_iam_admin,
    create_iam_session,
    get_iam_session_from_request,
)
from apps.accounts.models import (
    ApprovedDevice,
    AuthenticationPolicy,
    AuthenticationTransaction,
    AuthEvent,
    IAMRecoveryCase,
    IAMSession,
    SecurityAlert,
    UserSession,
)

logger = logging.getLogger(__name__)


def _safe_return_url(request, default: str) -> str:
    """Return the POSTed return_url only if it stays on this host, else the default."""
    from django.utils.http import url_has_allowed_host_and_scheme

    candidate = request.POST.get("return_url") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return default


def _require_iam_admin_session(request):
    """Enforces active IAM administrator session. Redirects unauthenticated requests to IAM login."""
    session = get_iam_session_from_request(request)
    if not session or not session.is_active:
        login_url = reverse("iam:login")
        return None, redirect(f"{login_url}?next={request.path}")
    request.iam_admin = session.iam_admin
    request.iam_session = session
    request.user = session.iam_admin.user
    return session, None


# ==============================================================================
# 1. AUTHENTICATION CENTRE LANDING
# ==============================================================================


def auth_centre_landing_view(request):
    """
    Authentication Centre Landing Page (Sequence 1 Contract Section 3).
    Displays EXACTLY TWO visibly separate sub-options:
    1. Authentication Control (Workspace employee domain)
    2. IAM Authentication (IAM administrator domain)
    """
    session, redir = _require_iam_admin_session(request)
    if redir:
        return redir

    ws_metrics = get_auth_overview_metrics(
        AuthenticationPolicy.Domain.WORKSPACE, time_window_hours=24
    )
    iam_metrics = get_auth_overview_metrics(AuthenticationPolicy.Domain.IAM, time_window_hours=24)
    probes = probe_all_dependencies()
    healthy_deps = sum(1 for p in probes if p["status"] == "healthy")

    context = {
        "iam_admin": session.iam_admin,
        "ws_metrics": ws_metrics,
        "iam_metrics": iam_metrics,
        "total_deps": len(probes),
        "healthy_deps": healthy_deps,
        "now": timezone.now(),
    }
    return render(request, "accounts/admin/auth_centre_landing.html", context)


# ==============================================================================
# 2. WORKSPACE AUTHENTICATION CONTROL DASHBOARD
# ==============================================================================


def auth_control_workspace_view(request):
    """
    Workspace Employee Authentication Control Dashboard.
    Contains: Overview, Live Activity (1s HTMX polling), Records, Controls,
    Policies, Dependencies, Audit History, and Testing Evidence.
    """
    session, redir = _require_iam_admin_session(request)
    if redir:
        return redir

    active_tab = request.GET.get("tab") or "overview"
    time_window_hours = int(request.GET.get("hours") or 24)

    metrics = get_auth_overview_metrics(
        AuthenticationPolicy.Domain.WORKSPACE, time_window_hours=time_window_hours
    )
    live_activity = get_live_activity_feed(AuthenticationPolicy.Domain.WORKSPACE, limit=20)
    policy = get_active_policy(AuthenticationPolicy.Domain.WORKSPACE)
    policy_history = AuthenticationPolicy.objects.filter(
        domain=AuthenticationPolicy.Domain.WORKSPACE
    ).order_by("-version")[:10]
    probes = probe_all_dependencies()
    alerts = SecurityAlert.objects.filter(
        category__in=[
            SecurityAlert.Category.REPEATED_AUTH_FAILURES,
            SecurityAlert.Category.ACCOUNT_LOCKOUT,
        ]
    ).order_by("-created_at")[:10]
    audit_events = AuthEvent.objects.order_by("-time")[:25]
    active_sessions = UserSession.objects.all().select_related("user", "device")[:15]
    approved_devices = ApprovedDevice.objects.all().order_by("-last_used_time")[:15]

    # Records search
    record_query = request.GET.get("q", "")
    record_outcome = request.GET.get("outcome", "")
    record_stage = request.GET.get("stage", "")
    records = search_auth_records(
        domain=AuthenticationPolicy.Domain.WORKSPACE,
        query=record_query,
        outcome=record_outcome,
        stage=record_stage,
        time_window_hours=time_window_hours,
        limit=50,
    )

    context = {
        "domain": "workspace",
        "domain_title": "Authentication Control — Workspace Employees",
        "iam_admin": session.iam_admin,
        "active_tab": active_tab,
        "metrics": metrics,
        "live_activity": live_activity,
        "policy": policy,
        "policy_history": policy_history,
        "probes": probes,
        "alerts": alerts,
        "audit_events": audit_events,
        "records": records,
        "record_query": record_query,
        "record_outcome": record_outcome,
        "record_stage": record_stage,
        "active_sessions": active_sessions,
        "approved_devices": approved_devices,
        "time_window_hours": time_window_hours,
        "now": timezone.now(),
    }
    return render(request, "accounts/admin/auth_dashboard_workspace.html", context)


# ==============================================================================
# 3. IAM AUTHENTICATION DASHBOARD
# ==============================================================================


def auth_iam_view(request):
    """
    Dedicated IAM Administrator Authentication Dashboard.
    Contains: Overview, Live Activity (1s HTMX polling), IAM Presences (5s heartbeat),
    Records, Controls, Policies (16-char min), Dependencies, Audit History, and Testing Evidence.
    """
    session, redir = _require_iam_admin_session(request)
    if redir:
        return redir

    active_tab = request.GET.get("tab") or "overview"
    time_window_hours = int(request.GET.get("hours") or 24)

    metrics = get_auth_overview_metrics(
        AuthenticationPolicy.Domain.IAM, time_window_hours=time_window_hours
    )
    live_activity = get_live_activity_feed(AuthenticationPolicy.Domain.IAM, limit=20)
    presences = get_all_admin_presences(limit=15)
    policy = get_active_policy(AuthenticationPolicy.Domain.IAM)
    policy_history = AuthenticationPolicy.objects.filter(
        domain=AuthenticationPolicy.Domain.IAM
    ).order_by("-version")[:10]
    probes = probe_all_dependencies()
    alerts = SecurityAlert.objects.filter(
        category__in=[
            SecurityAlert.Category.REPEATED_AUTH_FAILURES,
            SecurityAlert.Category.ACCOUNT_LOCKOUT,
        ]
    ).order_by("-created_at")[:10]
    recovery_cases = IAMRecoveryCase.objects.all().order_by("-created_at")[:15]
    audit_events = AuthEvent.objects.filter(employee_id__startswith="IAM").order_by("-time")[:25]
    if not audit_events.exists():
        audit_events = AuthEvent.objects.order_by("-time")[:25]
    active_iam_sessions = IAMSession.objects.filter(is_active=True).select_related("iam_admin")[:15]

    # Records search
    record_query = request.GET.get("q", "")
    record_outcome = request.GET.get("outcome", "")
    record_stage = request.GET.get("stage", "")
    records = search_auth_records(
        domain=AuthenticationPolicy.Domain.IAM,
        query=record_query,
        outcome=record_outcome,
        stage=record_stage,
        time_window_hours=time_window_hours,
        limit=50,
    )

    context = {
        "domain": "iam",
        "domain_title": "IAM Authentication — Administrator Security",
        "iam_admin": session.iam_admin,
        "active_tab": active_tab,
        "metrics": metrics,
        "live_activity": live_activity,
        "presences": presences,
        "policy": policy,
        "policy_history": policy_history,
        "probes": probes,
        "alerts": alerts,
        "recovery_cases": recovery_cases,
        "audit_events": audit_events,
        "records": records,
        "record_query": record_query,
        "record_outcome": record_outcome,
        "record_stage": record_stage,
        "active_iam_sessions": active_iam_sessions,
        "time_window_hours": time_window_hours,
        "now": timezone.now(),
    }
    return render(request, "accounts/admin/auth_dashboard_iam.html", context)


# ==============================================================================
# 4. HTMX LIVE ACTIVITY & PRESENCE FEEDS
# ==============================================================================


def _parse_feed_limit(raw, default: int = 20, maximum: int = 100) -> int:
    """Return a safe row limit: non-numbers fall back to the default, values are clamped to 1..maximum."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


def auth_live_activity_feed_view(request):
    """
    HTMX partial view returning live transactions table rows.
    Polled every 1 second by the dashboard active monitor.
    Pure read-only endpoint: zero database row creation per poll.
    """
    _session, redir = _require_iam_admin_session(request)
    if redir:
        return redir
    domain = request.GET.get("domain", "workspace").lower().strip()
    limit = _parse_feed_limit(request.GET.get("limit"))
    transactions = get_live_activity_feed(domain, limit=limit)
    return render(
        request,
        "accounts/admin/partials/auth_live_activity_rows.html",
        {"transactions": transactions, "domain": domain, "now": timezone.now()},
    )


def auth_presence_feed_view(request):
    """
    HTMX partial view returning current IAM administrator presences.
    Distinguishes ACTIVE (heartbeat <= 15s), STALE (> 15s), and REVOKED/EXPIRED.
    """
    _session, redir = _require_iam_admin_session(request)
    if redir:
        return redir
    presences = get_all_admin_presences(limit=15)
    return render(
        request,
        "accounts/admin/partials/auth_presence_rows.html",
        {"presences": presences, "now": timezone.now()},
    )


@require_POST
def iam_heartbeat_view(request):
    """
    IAM Administrator Presence Heartbeat (Sequence 1 Contract Section 16).
    POST every 5 seconds from authenticated IAM pages.
    Requires verified IAM session. Workspace sessions cannot satisfy this endpoint.
    """
    session = get_iam_session_from_request(request)
    if not session or not session.is_active:
        return JsonResponse(
            {"error": "Unauthorized", "detail": "Valid IAM session required."}, status=401
        )

    page_key = request.POST.get("page_key", "console")
    action_category = request.POST.get("action_category", "view")
    device_label = request.POST.get("device_label", "")
    last_action = request.POST.get("last_action", "navigation")
    last_outcome = request.POST.get("last_outcome", "success")

    presence = record_admin_heartbeat(
        session=session,
        page_key=page_key,
        action_category=action_category,
        device_label=device_label,
        last_action=last_action,
        last_outcome=last_outcome,
    )

    return JsonResponse(
        {
            "status": "ok",
            "admin_id": session.iam_admin.admin_id,
            "presence_status": presence.presence_status,
            "heartbeat_at": presence.last_heartbeat.isoformat(),
        }
    )


# ==============================================================================
# 5. TRANSACTION DETAIL INVESTIGATION
# ==============================================================================


def auth_transaction_detail_view(request, transaction_id: str):
    """
    Detailed Transaction Investigation View.
    Displays complete safe stage timeline, evaluated policy version, risk decision,
    device context, and durable audit event linkage. Never displays plaintext secrets.
    """
    session, redir = _require_iam_admin_session(request)
    if redir:
        return redir

    tx = get_object_or_404(AuthenticationTransaction, transaction_id=transaction_id)
    linked_events = AuthEvent.objects.filter(reference_id=tx.transaction_id).order_by("time")

    context = {
        "iam_admin": session.iam_admin,
        "tx": tx,
        "stages": tx.stages,
        "linked_events": linked_events,
        "now": timezone.now(),
    }
    return render(request, "accounts/admin/auth_transaction_detail.html", context)


# ==============================================================================
# 6. THE EIGHT REAL CONTROL ACTIONS (CSRF-PROTECTED POST DISPATCHER)
# ==============================================================================


@require_POST
def auth_control_action_view(request):  # noqa: C901 (refactor deferred, behavior covered by tests)
    """
    CSRF-protected action dispatcher for the Eight Real Controls (Sequence 1 Contract Section 13).
    Zero decorative buttons: every action executes an authorized backend mutation and records an audit trail.
    """
    session, redir = _require_iam_admin_session(request)
    if redir:
        return redir

    action_type = request.POST.get("action_type", "").strip()
    return_url = _safe_return_url(request, reverse("iam:auth_landing"))
    reason = request.POST.get("reason", "").strip() or "Standard administrative action"
    actor_admin = session.iam_admin

    try:
        if action_type == "revoke_session":
            session_ref = request.POST.get("session_ref", "").strip()
            domain = request.POST.get("domain", "workspace").strip()
            res = execute_revoke_session(session_ref, domain, actor_admin, reason)
            messages.success(request, res["message"])

        elif action_type == "revoke_device":
            device_ref = request.POST.get("device_ref", "").strip()
            res = execute_revoke_device(device_ref, actor_admin, reason)
            messages.success(request, res["message"])

        elif action_type == "temporary_restriction":
            identity_ref = request.POST.get("identity_ref", "").strip()
            domain = request.POST.get("domain", "workspace").strip()
            duration_minutes = int(request.POST.get("duration_minutes", 15))
            res = execute_temporary_restriction(
                identity_ref, domain, actor_admin, duration_minutes, reason
            )
            messages.success(request, res["message"])

        elif action_type == "release_lock":
            lock_key = request.POST.get("lock_key", "").strip()
            res = execute_release_lock(lock_key, actor_admin, reason)
            messages.success(request, res["message"])

        elif action_type == "acknowledge_alert":
            alert_id = int(request.POST.get("alert_id", 0))
            res = execute_acknowledge_alert(alert_id, actor_admin, reason)
            messages.success(request, res["message"])

        elif action_type == "export_records":
            domain = request.POST.get("domain", "workspace").strip()
            filters = {
                "query": request.POST.get("q", ""),
                "outcome": request.POST.get("outcome", ""),
            }
            res = execute_export_records(domain, filters, actor_admin)
            messages.success(
                request,
                f"Export generated: {res['record_count']} records (Digest: {res['file_digest'][:12]}...)",
            )

        elif action_type == "start_investigation":
            transaction_id = request.POST.get("transaction_id", "").strip()
            title = request.POST.get("title", "").strip()
            notes = request.POST.get("notes", "").strip()
            res = execute_start_investigation(transaction_id, actor_admin, title, notes)
            messages.success(request, res["message"])

        elif action_type == "update_policy":
            domain = request.POST.get("domain", "workspace").strip()
            password_min_length = int(request.POST.get("password_min_length", 12))
            failed_login_threshold = int(request.POST.get("failed_login_threshold", 5))
            lockout_window_minutes = int(request.POST.get("lockout_window_minutes", 15))
            lockout_duration_minutes = int(request.POST.get("lockout_duration_minutes", 15))
            idle_session_timeout_minutes = int(request.POST.get("idle_session_timeout_minutes", 15))
            absolute_session_timeout_hours = int(
                request.POST.get("absolute_session_timeout_hours", 12)
            )
            mfa_required = (
                request.POST.get("mfa_required") == "on"
                or request.POST.get("mfa_required") == "true"
            )
            device_approval_required = (
                request.POST.get("device_approval_required") == "on"
                or request.POST.get("device_approval_required") == "true"
            )
            justification = request.POST.get("justification", "").strip()

            new_pol = save_policy_version(
                domain=domain,
                password_min_length=password_min_length,
                failed_login_threshold=failed_login_threshold,
                lockout_window_minutes=lockout_window_minutes,
                lockout_duration_minutes=lockout_duration_minutes,
                idle_session_timeout_minutes=idle_session_timeout_minutes,
                absolute_session_timeout_hours=absolute_session_timeout_hours,
                mfa_required=mfa_required,
                device_approval_required=device_approval_required,
                editor_user=actor_admin.user,
                justification=justification,
            )
            messages.success(
                request,
                f"Policy for [{domain.upper()}] updated to version {new_pol.version} successfully.",
            )

        else:
            messages.error(request, f"Unknown action type: '{action_type}'.")

    except ValidationError as exc:
        messages.error(request, f"Validation error: {exc}")
    except PermissionDenied as exc:
        messages.error(request, f"Permission denied: {exc}")
    except Exception:
        logger.exception("Error executing auth control action '%s'", action_type)
        messages.error(
            request, "Operation failed. The error was logged; please try again or contact support."
        )

    return redirect(return_url)


# ==============================================================================
# 7. DEDICATED CANONICAL IAM SIGN-IN & RECOVERY PORTAL
# ==============================================================================


def iam_sign_in_canonical_view(request):
    """
    Dedicated IAM Administrator Sign-In Route: /auth/sign-in/ (Section 6.1).
    1. Dedicated IAM ID.
    2. Min 16-character IAM password.
    3. Fresh RFC 6238 TOTP.
    4. Account lifecycle, admin eligibility, L1 assurance.
    5. Host-only IAM session cookie (__Host-salow_iam_session).
    """
    session = get_iam_session_from_request(request)
    if session:
        return redirect("iam:auth_landing")

    error = None
    if request.method == "POST":
        admin_id = request.POST.get("admin_id", "").strip().upper()
        password = request.POST.get("password", "")
        totp_code = request.POST.get("totp_code", "").strip()
        client_ip = getattr(request, "client_ip", None) or request.META.get(
            "REMOTE_ADDR", "127.0.0.1"
        )

        tx = start_auth_transaction(
            domain="iam",
            identity_ref=admin_id,
            client_ip=client_ip,
        )

        if not admin_id or not password or not totp_code:
            error = "Dedicated IAM ID, Password, and TOTP code are required."
            record_transaction_stage(
                tx.transaction_id, "password_check", "FAIL", "Missing required credential fields"
            )
            complete_auth_transaction(tx.transaction_id, "failed", reason_category="missing_fields")
        elif len(password) < 16:
            error = "IAM Administrator password must meet the 16-character minimum requirement."
            record_transaction_stage(
                tx.transaction_id, "password_check", "FAIL", "Password below 16-character minimum"
            )
            complete_auth_transaction(
                tx.transaction_id, "failed", reason_category="password_length_violation"
            )
        else:
            try:
                record_transaction_stage(
                    tx.transaction_id, "password_check", "PASS", "Password verified"
                )
                iam_admin = authenticate_iam_admin(
                    admin_id=admin_id,
                    password=password,
                    totp_code=totp_code,
                    client_ip=client_ip,
                )
                record_transaction_stage(
                    tx.transaction_id, "mfa_verification", "PASS", "TOTP valid"
                )
                record_transaction_stage(
                    tx.transaction_id, "lifecycle", "PASS", f"Status: {iam_admin.status}"
                )

                session = create_iam_session(iam_admin, request, assurance_level=1)
                complete_auth_transaction(
                    tx.transaction_id,
                    "success",
                    reason_category="credentials_and_mfa_valid",
                    session_ref=session.session_key,
                )

                next_url = request.GET.get("next") or reverse("iam:auth_landing")
                if not url_has_allowed_host_and_scheme(
                    next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
                ):
                    next_url = reverse("iam:auth_landing")
                response = redirect(next_url)

                cookie_name = IAM_SESSION_COOKIE_NAME
                secure = getattr(settings, "SESSION_COOKIE_SECURE", False)
                response.set_cookie(
                    cookie_name,
                    # The cookie carries the raw bearer token; only its digest is stored.
                    getattr(request, "session_key_iam", None) or session.session_key,
                    httponly=True,
                    secure=secure,
                    samesite="Strict" if secure else "Lax",
                    path="/",
                )
                return response
            except AuthException as exc:
                error = exc.user_message
                record_transaction_stage(tx.transaction_id, "password_check", "FAIL", str(exc))
                complete_auth_transaction(
                    tx.transaction_id, "failed", reason_category="auth_exception"
                )
            except Exception as exc:
                error = "Authentication failed. Please verify credentials."
                record_transaction_stage(tx.transaction_id, "password_check", "FAIL", str(exc))
                complete_auth_transaction(
                    tx.transaction_id, "failed", reason_category="unexpected_error"
                )

    return render(
        request,
        "accounts/admin/iam_sign_in_canonical.html",
        {"error": error},
    )


def iam_recovery_portal_view(request):
    """
    Dedicated IAM Account Recovery Portal: /auth/recovery/ (Section 6.3).
    Starts an isolated IAM administrator recovery case with generic acknowledgment.
    Requires dual-control identity proofing before one-time reset token issuance.
    """
    submitted = False
    case_ref = None

    if request.method == "POST":
        identity_ref = request.POST.get("identity_ref", "").strip().upper()
        note = request.POST.get("note", "").strip()

        if identity_ref:
            case = create_iam_recovery_case(identity_ref, note)
            submitted = True
            case_ref = case.case_id[:8]
        else:
            messages.error(request, "Please provide an IAM administrator ID.")

    return render(
        request,
        "accounts/admin/iam_recovery_portal.html",
        {"submitted": submitted, "case_ref": case_ref},
    )


@require_POST
def iam_recovery_case_action_view(request):
    """
    Administrative review and dual-control approval for IAM recovery cases.
    """
    session, redir = _require_iam_admin_session(request)
    if redir:
        return redir

    case_id = request.POST.get("case_id", "").strip()
    action = request.POST.get("action", "").strip()
    reason = request.POST.get("reason", "").strip()
    actor_admin = session.iam_admin
    return_url = _safe_return_url(request, reverse("iam:auth_iam"))

    try:
        if action == "approve":
            case = approve_iam_recovery_case(case_id, actor_admin, note=reason)
            messages.success(
                request, f"Case [{case.case_id}] updated to state: {case.approval_state}."
            )
        elif action == "reject":
            case = reject_iam_recovery_case(case_id, actor_admin, rejection_reason=reason)
            messages.success(request, f"Case [{case.case_id}] rejected.")
        else:
            messages.error(request, f"Unknown action: {action}")
    except PermissionDenied as exc:
        messages.error(request, f"Permission Denied: {exc}")
    except Exception:
        logger.exception("Error executing recovery case action '%s'", action)
        messages.error(
            request,
            "Approval operation failed. The error was logged; please try again or contact support.",
        )

    return redirect(return_url)


def sessions_investigation_view(request):
    """
    Authorized IAM & Workspace Session Investigation: /console/sessions/
    """
    session, redir = _require_iam_admin_session(request)
    if redir:
        return redir

    domain = request.GET.get("domain", "all").lower().strip()
    iam_sessions = []
    ws_sessions = []

    if domain in ("all", "iam"):
        iam_sessions = (
            IAMSession.objects.all().select_related("iam_admin").order_by("-last_activity")[:30]
        )
    if domain in ("all", "workspace"):
        ws_sessions = (
            UserSession.objects.all()
            .select_related("user", "device")
            .order_by("-last_activity")[:30]
        )

    context = {
        "iam_admin": session.iam_admin,
        "domain": domain,
        "iam_sessions": iam_sessions,
        "ws_sessions": ws_sessions,
        "now": timezone.now(),
    }
    return render(request, "accounts/admin/sessions_investigation.html", context)


def iam_live_events_view(request):
    """JSON action feed for IAM dashboards. Cursor via ?after=<id>; polled about once a second.

    Needs a valid, active IAM session on every request (revocation takes effect at the
    next poll). Workspace sessions get 401."""
    from apps.accounts.auth.live_events import read_events

    session = get_iam_session_from_request(request)
    if not session or not session.is_active:
        return JsonResponse({"error": "Unauthorized"}, status=401)
    after = request.GET.get("after") or request.headers.get("Last-Event-ID")
    after_id = int(after) if after and after.isdigit() else None
    domain = request.GET.get("domain", "all").lower().strip()
    if domain not in ("all", "iam", "workspace", "unclassified"):
        domain = "all"
    try:
        limit = int(request.GET.get("limit", "50"))
    except ValueError:
        limit = 50
    data = read_events(after_id, domain, limit)
    data["server_time"] = timezone.now().isoformat()
    response = JsonResponse(data)
    response["Cache-Control"] = "no-store"
    return response


async def iam_live_stream_view(request):
    """SSE push of the IAM action feed. ASGI only: under WSGI this answers 404 and the page
    keeps polling iam_live_events_view. Same authorization as the poll, rechecked every 5 s."""
    from asgiref.sync import sync_to_async
    from django.http import Http404, StreamingHttpResponse

    from apps.accounts.auth import live_stream

    if not hasattr(request, "scope"):
        raise Http404("Streaming needs an ASGI server")
    session = await sync_to_async(get_iam_session_from_request)(request)
    if not session or not session.is_active:
        return JsonResponse({"error": "Unauthorized"}, status=401)
    if not live_stream.admit(session.pk):
        return JsonResponse({"error": "Too many streams, use polling"}, status=503)
    after = request.GET.get("after") or request.headers.get("Last-Event-ID")
    after_id = int(after) if after and after.isdigit() else None
    domain = request.GET.get("domain", "all").lower().strip()
    if domain not in ("all", "iam", "workspace", "unclassified"):
        domain = "all"
    role = await sync_to_async(lambda: session.iam_admin.role)()
    response = StreamingHttpResponse(
        live_stream.stream(session.pk, role, domain, after_id),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    return response
