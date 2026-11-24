"""
Salow Workspace — Workday & Business Date Synchronization Utilities.
Enforces:
1. Asia/Kolkata authoritative business timezone.
2. Midnight preservation: open attendance sessions crossing midnight remain one continuous session.
3. Daily dashboard context refresh preserving all historical attendance, posts, certificates, and audit logs.
"""

from datetime import date, datetime, time
from typing import Any, Optional, Tuple
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.attendance.models import WorkSession
from apps.certificates.models import Certificate
from apps.core.models import AuditLog
from apps.posts.models import Post

KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


def get_current_kolkata_datetime(dt: Optional[datetime] = None) -> datetime:
    """Returns the current instant in Asia/Kolkata timezone."""
    now = dt or timezone.now()
    return now.astimezone(KOLKATA_TZ)


def get_kolkata_work_date(dt: Optional[datetime] = None) -> date:
    """Converts a UTC datetime instant to the business work date in Asia/Kolkata."""
    return get_current_kolkata_datetime(dt).date()


def get_workday_boundaries(work_date: date) -> Tuple[datetime, datetime]:
    """Returns the start (00:00:00) and end (23:59:59.999999) of a business day in Asia/Kolkata."""
    start = datetime.combine(work_date, time.min, tzinfo=KOLKATA_TZ)
    end = datetime.combine(work_date, time.max, tzinfo=KOLKATA_TZ)
    return start, end


def is_session_crossing_midnight(session: WorkSession, check_time: Optional[datetime] = None) -> bool:
    """
    Checks if an open session started on a previous calendar date in Asia/Kolkata.
    Per v4.0 invariant: Midnight does NOT close an attendance session.
    """
    now = check_time or timezone.now()
    check_in_date = get_kolkata_work_date(session.check_in_at)
    current_date = get_kolkata_work_date(now)
    return current_date > check_in_date


def refresh_daily_workspace_context(
    user: CustomUser,
    current_instant: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    Refreshes daily dashboard context for the user:
    - Today's date in Asia/Kolkata.
    - Today's attendance status (open, completed, or not started).
    - Daily post prompt & status for today.
    - Preserves historical records: asserts and counts historical posts, attendance, certificates.
    """
    now = current_instant or timezone.now()
    today = get_kolkata_work_date(now)

    # 1. Today's Attendance State
    # Note: open session crossing midnight belongs to its original check_in work_date
    open_session = WorkSession.objects.filter(
        user=user,
        state=WorkSession.State.OPEN,
    ).first()

    today_completed_sessions = WorkSession.objects.filter(
        user=user,
        work_date=today,
        state=WorkSession.State.COMPLETED,
    )

    attendance_status = "not_started"
    if open_session:
        attendance_status = "checked_in"
    elif today_completed_sessions.exists():
        attendance_status = "completed"

    # 2. Daily Post Status
    today_post = Post.objects.filter(
        author=user,
        work_date=today,
    ).first()

    post_status = "submitted" if today_post else "pending"

    # 3. Preserved Historical Evidence Counters (Historical data MUST NOT be deleted)
    historical_attendance_count = WorkSession.objects.filter(user=user).exclude(work_date=today).count()
    historical_posts_count = Post.objects.filter(author=user).exclude(work_date=today).count()
    total_certificates_count = Certificate.objects.filter(recipient=user).count()
    total_audit_count = AuditLog.objects.count()

    return {
        "work_date": today,
        "work_date_formatted": today.strftime("%A, %d %B %Y"),
        "timezone": "Asia/Kolkata",
        "attendance_status": attendance_status,
        "open_session": open_session,
        "today_post": today_post,
        "post_status": post_status,
        "counts": {
            "historical_attendance": historical_attendance_count,
            "historical_posts": historical_posts_count,
            "certificates": total_certificates_count,
            "audit_logs": total_audit_count,
        },
    }
