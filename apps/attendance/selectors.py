"""
Attendance Selectors Module.
Query and read interfaces for attendance records, reports, and exception queues.
"""

from datetime import date, timedelta
from typing import Optional

from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.attendance.models import (
    AttendanceFallbackRequest,
    WorkSession,
)


def get_open_session(user: CustomUser) -> Optional[WorkSession]:
    """Returns the current open work session for the given user, if any."""
    return WorkSession.objects.filter(user=user, state=WorkSession.State.OPEN).first()


def get_user_sessions(user: CustomUser, limit: int = 10) -> QuerySet[WorkSession]:
    """Returns the most recent work sessions for the user."""
    return WorkSession.objects.filter(user=user).order_by("-check_in_at")[:limit]


def get_pending_fallback_requests(
    user: Optional[CustomUser] = None,
) -> QuerySet[AttendanceFallbackRequest]:
    """Returns pending fallback requests, optionally filtered by employee."""
    qs = AttendanceFallbackRequest.objects.filter(status=AttendanceFallbackRequest.Status.PENDING)
    if user:
        qs = qs.filter(user=user)
    return qs.select_related("user").order_by("-created_at")


def get_attendance_report(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user: Optional[CustomUser] = None,
) -> dict:
    """
    Generates an aggregated attendance report for a specified date range and optional user.
    """
    end = end_date or timezone.localdate()
    start = start_date or (end - timedelta(days=30))

    qs = WorkSession.objects.filter(work_date__gte=start, work_date__lte=end)
    if user:
        qs = qs.filter(user=user)

    total_sessions = qs.count()
    completed_sessions = qs.filter(state=WorkSession.State.COMPLETED).count()
    open_sessions = qs.filter(state=WorkSession.State.OPEN).count()
    manual_sessions = qs.filter(
        verification_method__in=[
            WorkSession.VerificationMethod.MANUAL_APPROVED,
            WorkSession.VerificationMethod.ADMIN_ENTRY,
        ]
    ).count()

    total_seconds = 0.0
    for s in qs.filter(check_out_at__isnull=False):
        total_seconds += (s.check_out_at - s.check_in_at).total_seconds()

    total_hours = round(total_seconds / 3600.0, 2)

    return {
        "start_date": start,
        "end_date": end,
        "total_sessions": total_sessions,
        "completed_sessions": completed_sessions,
        "open_sessions": open_sessions,
        "manual_exceptions_count": manual_sessions,
        "total_hours_worked": total_hours,
    }


def get_month_history(user: CustomUser, year: int, month: int) -> dict:
    """The person's own sessions for one calendar month (by business date, Asia/Kolkata),
    oldest first, with day totals. Open sessions are listed but add no hours."""
    from calendar import monthrange
    from zoneinfo import ZoneInfo

    kolkata = ZoneInfo("Asia/Kolkata")
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    rows = []
    days: dict = {}
    total_seconds = 0.0
    for s in WorkSession.objects.filter(
        user=user, work_date__gte=first, work_date__lte=last
    ).order_by("work_date", "check_in_at"):
        secs = (s.check_out_at - s.check_in_at).total_seconds() if s.check_out_at else None
        if secs is not None:
            total_seconds += secs
            days[s.work_date] = days.get(s.work_date, 0.0) + secs
        rows.append(
            {
                "date": s.work_date,
                "check_in": s.check_in_at.astimezone(kolkata).strftime("%H:%M"),
                "check_out": s.check_out_at.astimezone(kolkata).strftime("%H:%M")
                if s.check_out_at
                else None,
                "hours": round(secs / 3600.0, 2) if secs is not None else None,
                "method": s.get_verification_method_display(),
                "state": s.get_state_display(),
                "open": s.state == WorkSession.State.OPEN,
            }
        )
    prev_month = (first - timedelta(days=1)).replace(day=1)
    next_month = (last + timedelta(days=1)).replace(day=1)
    return {
        "first": first,
        "rows": rows,
        "days_worked": len(days),
        "total_hours": round(total_seconds / 3600.0, 2),
        "prev": prev_month.strftime("%Y-%m"),
        "next": next_month.strftime("%Y-%m"),
    }
