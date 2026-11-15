from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.attendance.models import AttendanceFallbackRequest
from apps.attendance.selectors import (
    get_attendance_report,
    get_open_session,
    get_user_sessions,
)
from apps.attendance.services import (
    check_in,
    check_out,
    create_fallback_request,
    get_current_kolkata_date,
)
from apps.face.engine.stub import StubVerifier


@login_required
def panel_view(request):
    """
    Renders personal attendance dashboard with active session, history, and fallback requests.
    """
    open_session = get_open_session(request.user)
    recent_sessions = get_user_sessions(request.user, limit=10)
    my_requests = AttendanceFallbackRequest.objects.filter(user=request.user).order_by(
        "-created_at"
    )[:5]

    context = {
        "open_session": open_session,
        "recent_sessions": recent_sessions,
        "my_requests": my_requests,
        "dev_verifier_active": getattr(settings, "DEV_VERIFIER", False),
    }
    return render(request, "attendance/panel.html", context)


@login_required
def check_in_view(request):
    """
    Executes check-in via StubVerifier (in dev mode) or production engine.
    """
    if request.method != "POST":
        return redirect("attendance:panel")

    try:
        is_dev = getattr(settings, "DEV_VERIFIER", False)
        verifier = StubVerifier(allowed=is_dev)
        result = verifier.verify(
            user_id=request.user.employee_id,
            challenge_nonce="m0-dev-nonce",
            frames=[],
        )
        session = check_in(user=request.user, verification=result)
        messages.success(
            request,
            f"Successfully checked in at {session.check_in_at.strftime('%H:%M:%S UTC')}.",
        )
    except ValidationError as err:
        messages.error(request, str(err.message if hasattr(err, "message") else err))
    except Exception as exc:
        messages.error(request, f"Check-in failed: {exc}")

    return redirect("attendance:panel")


@login_required
def check_out_view(request):
    """
    Executes check-out to close active attendance session.
    """
    if request.method != "POST":
        return redirect("attendance:panel")

    try:
        session = check_out(user=request.user)
        messages.success(
            request,
            f"Successfully checked out at {session.check_out_at.strftime('%H:%M:%S UTC')}.",
        )
    except ValidationError as err:
        messages.error(request, str(err.message if hasattr(err, "message") else err))
    except Exception as exc:
        messages.error(request, f"Check-out failed: {exc}")

    return redirect("attendance:panel")


@login_required
def fallback_request_view(request):
    """
    Submit manual attendance request when biometric verification fails.
    """
    if request.method == "POST":
        req_type = request.POST.get("request_type", "check_in")
        reason = request.POST.get("reason", "").strip()
        requested_time = timezone.now()
        work_date = get_current_kolkata_date(requested_time)

        try:
            create_fallback_request(
                user=request.user,
                request_type=req_type,
                work_date=work_date,
                requested_time=requested_time,
                reason=reason,
            )
            messages.success(
                request,
                "Manual attendance request submitted successfully. Pending manager review.",
            )
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, "message") else e))

    return redirect("attendance:panel")


@login_required
def report_view(request):
    """
    Aggregated attendance report view for the user.
    """
    report = get_attendance_report(user=request.user)
    context = {
        "report": report,
        "recent_sessions": get_user_sessions(request.user, limit=30),
    }
    return render(request, "attendance/report.html", context)
