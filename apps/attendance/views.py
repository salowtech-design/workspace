from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.accounts.models import AuthEvent, CustomUser, UserSession
from apps.attendance.models import (
    AttendanceCorrection,
    AttendanceFallbackRequest,
    WorkSession,
)
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
    review_fallback_request,
)
from apps.core.workday import refresh_daily_workspace_context
from apps.face.engine.stub import StubVerifier


@login_required
def panel_view(request):
    """
    Renders personal attendance dashboard with active session, history, and fallback requests.
    Synchronizes daily workday context (Asia/Kolkata) preserving historical records (Section 11).
    """
    workday_ctx = refresh_daily_workspace_context(request.user)
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
        "workday": workday_ctx,
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
        if is_dev:
            verifier = StubVerifier(allowed=is_dev)
            result = verifier.verify(
                user_id=request.user.employee_id,
                challenge_nonce="m0-dev-nonce",
                frames=[],
            )
        else:
            from apps.face.models import FaceVerificationLog
            from apps.face.services import verify_face

            frame_data = request.POST.get("frame_data")
            result = verify_face(
                user=request.user,
                purpose=FaceVerificationLog.Purpose.ATTENDANCE_CHECK_IN,
                frame_data={"frame": frame_data} if frame_data else None,
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


@login_required
def attendance_admin_panel_view(request):
    """
    Ordinary Workspace Admin Panel for Attendance & Daily Operations.
    Segregated from central Identity & Security Management.
    Receives approved, redacted, read-only security summaries.
    """
    actor: CustomUser = request.user
    allowed_roles = (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.HR,
        CustomUser.Role.MANAGER,
    )
    if actor.role not in allowed_roles:
        raise PermissionDenied("Access restricted to administrative and management personnel.")

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "review_fallback":
            request_id = request.POST.get("request_id")
            decision = request.POST.get("decision", "").strip().lower()
            reason = request.POST.get("reason", "").strip()
            try:
                if not request_id:
                    raise ValueError("Fallback request ID is required.")
                review_fallback_request(
                    request_id=int(request_id),
                    reviewer=actor,
                    approved=(decision == "approve"),
                    review_notes=reason,
                )
                messages.success(
                    request, f"Fallback request #{request_id} successfully {decision}d."
                )
            except (ValidationError, PermissionDenied, ValueError, TypeError) as exc:
                messages.error(request, str(exc))
            return redirect("attendance:admin_panel")

    # Business attendance metrics
    today = get_current_kolkata_date(timezone.now())
    today_sessions = WorkSession.objects.filter(work_date=today).select_related("user")
    pending_fallbacks = AttendanceFallbackRequest.objects.filter(
        status=AttendanceFallbackRequest.Status.PENDING
    ).select_related("user")
    corrections = AttendanceCorrection.objects.select_related(
        "session__user", "corrected_by"
    ).order_by("-created_at")[:10]

    # Redacted Read-Only Security Summary from Central IAM Authority
    total_staff = CustomUser.objects.count()
    active_sessions_count = UserSession.objects.count()
    events_24h = AuthEvent.objects.filter(time__gte=timezone.now() - timedelta(hours=24)).count()

    iam_summary = {
        "authority": "Salow IAM Platform (Central Monolith Authority)",
        "posture": "PROTECTED & OPERATIONAL",
        "total_identities": total_staff,
        "active_sessions": active_sessions_count,
        "events_24h": events_24h,
        "isolation_notice": (
            "IAM remains the single central identity and authentication authority. "
            "All identity provisioning, credential vaults, authenticator records, session revocations, "
            "and recovery operations are strictly managed in the dedicated Identity & Security Console. "
            "This panel receives only approved, redacted, read-only summaries."
        ),
    }

    context = {
        "today_sessions": today_sessions,
        "pending_fallbacks": pending_fallbacks,
        "corrections": corrections,
        "iam_summary": iam_summary,
        "actor": actor,
    }
    return render(request, "attendance/admin_panel.html", context)
