"""Date defaults follow the configured local time zone (Asia/Kolkata), not UTC."""

from datetime import datetime
from datetime import timezone as dt_timezone
from unittest import mock

import pytest
from django.utils import timezone

from apps.attendance.selectors import get_attendance_report

pytestmark = pytest.mark.django_db


def test_report_default_end_is_local_today_after_midnight_ist():
    # 20:00 UTC on 24 Nov is 01:30 IST on 25 Nov.
    fake_now = datetime(2026, 11, 24, 20, 0, tzinfo=dt_timezone.utc)
    with mock.patch("django.utils.timezone.now", return_value=fake_now):
        assert timezone.localdate() == datetime(2026, 11, 25).date()
        report = get_attendance_report()
    assert report["end_date"] == datetime(2026, 11, 25).date()
