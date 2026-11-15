from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from apps.attendance.selectors import get_open_session, get_user_sessions
from apps.attendance.services import check_in, check_out
from apps.face.engine.stub import StubVerifier


@login_required
def panel_view(request):
    """
    Renders personal attendance dashboard with active session and history.
    """
    open_session = get_open_session(request.user)
    recent_sessions = get_user_sessions(request.user, limit=10)

    context = {
        "open_session": open_session,
        "recent_sessions": recent_sessions,
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
        # M0 uses development stub verifier
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
