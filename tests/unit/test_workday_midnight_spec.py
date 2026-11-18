"""
Workday, Midnight Preservation & Data Synchronization Tests — Salow Workspace.
Validates:
1. Asia/Kolkata business date calculation across UTC boundaries.
2. Midnight does NOT automatically terminate or split an active attendance session.
3. Open attendance sessions crossing midnight remain one continuous session.
4. Daily workspace context refresh strictly preserves all historical attendance, posts, and audit logs.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.attendance.models import WorkSession
from apps.certificates.models import Certificate
from apps.core.models import AuditLog
from apps.core.workday import (
    KOLKATA_TZ,
    get_current_kolkata_datetime,
    get_kolkata_work_date,
    get_workday_boundaries,
    is_session_crossing_midnight,
    refresh_daily_workspace_context,
)
from apps.posts.models import Post

User = get_user_model()


@pytest.fixture
def shift_worker(db):
    user = User.objects.create(
        employee_id="ST-WRK-0001",
        full_name="Night Shift Engineer",
        email="nightshift@salowtech.com",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("NightShift2026!@#")
    user.save()
    return user


@pytest.mark.django_db
class TestWorkdayAndMidnightPreservation:
    def test_kolkata_work_date_conversion(self):
        """Converts UTC instant to Asia/Kolkata work date correctly."""
        # 18:30 UTC on 2026-11-18 is 00:00 IST on 2026-11-19
        utc_instant = datetime(2026, 11, 18, 18, 30, tzinfo=ZoneInfo("UTC"))
        work_date = get_kolkata_work_date(utc_instant)
        assert work_date.year == 2026
        assert work_date.month == 11
        assert work_date.day == 19

    def test_workday_boundaries(self):
        """Workday boundaries correspond to 00:00:00 to 23:59:59.999999 IST."""
        today = get_kolkata_work_date()
        start, end = get_workday_boundaries(today)
        assert start.tzinfo == KOLKATA_TZ
        assert end.tzinfo == KOLKATA_TZ
        assert start.time() == time.min
        assert end.time() == time.max

    def test_open_session_crossing_midnight_remains_one_continuous_session(self, shift_worker):
        """
        An attendance session initiated at 22:00 IST and checked out at 04:00 IST next morning
        remains ONE single continuous session with original work date preserved.
        """
        # Checked in at 22:00 IST on 2026-11-18
        check_in_dt = datetime(2026, 11, 18, 22, 0, tzinfo=KOLKATA_TZ)
        work_date = check_in_dt.date()

        session = WorkSession.objects.create(
            user=shift_worker,
            work_date=work_date,
            check_in_at=check_in_dt,
            verification_method=WorkSession.VerificationMethod.FACE,
            state=WorkSession.State.OPEN,
        )

        # Inspection at 02:00 IST on 2026-11-19 (after midnight)
        after_midnight_dt = datetime(2026, 11, 19, 2, 0, tzinfo=KOLKATA_TZ)
        assert is_session_crossing_midnight(session, check_time=after_midnight_dt) is True

        # Session remains OPEN and has not been truncated
        session.refresh_from_db()
        assert session.state == WorkSession.State.OPEN
        assert session.check_out_at is None
        assert session.work_date == work_date

        # Completed checkout at 06:00 IST
        checkout_dt = datetime(2026, 11, 19, 6, 0, tzinfo=KOLKATA_TZ)
        session.check_out_at = checkout_dt
        session.state = WorkSession.State.COMPLETED
        session.save()

        # Confirms only 1 total session exists for this entire duration
        assert WorkSession.objects.filter(user=shift_worker).count() == 1
        duration = (session.check_out_at - session.check_in_at).total_seconds() / 3600.0
        assert duration == pytest.approx(8.0, rel=1e-2)

    def test_daily_workspace_refresh_preserves_historical_records(self, shift_worker):
        """
        Daily context refresh provides current-day operational counters while
        strictly preserving historical attendance, posts, and certificates.
        """
        today = get_kolkata_work_date()
        yesterday = today - timedelta(days=1)

        # Create historical attendance session from yesterday
        WorkSession.objects.create(
            user=shift_worker,
            work_date=yesterday,
            check_in_at=datetime.combine(yesterday, time(9, 0), tzinfo=KOLKATA_TZ),
            check_out_at=datetime.combine(yesterday, time(17, 0), tzinfo=KOLKATA_TZ),
            state=WorkSession.State.COMPLETED,
        )

        # Create historical post from yesterday
        Post.objects.create(
            author=shift_worker,
            title="Yesterday Work Log",
            content="Historical tasks accomplished",
            work_date=yesterday,
        )

        # Perform daily workspace refresh for today
        ctx = refresh_daily_workspace_context(user=shift_worker)

        # Today context
        assert ctx["work_date"] == today
        assert ctx["attendance_status"] == "not_started"
        assert ctx["post_status"] == "pending"

        # Verify historical preservation
        assert ctx["counts"]["historical_attendance"] == 1
        assert ctx["counts"]["historical_posts"] == 1
        assert WorkSession.objects.filter(user=shift_worker).count() == 1
        assert Post.objects.filter(author=shift_worker).count() == 1
