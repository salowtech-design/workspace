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
    end = end_date or timezone.now().date()
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
