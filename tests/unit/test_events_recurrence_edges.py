import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from apps.events import recurrence as rec
from apps.events import services as es
from tests.unit.test_events_recurrence import UTC, W, _u, mk

NY = "America/New_York"


@pytest.mark.django_db
def test_until_series_stores_last_end_of_its_last_shown_occurrence_and_windows_use_it():
    a = _u("RE-1")
    ev = mk(a, "2026-12-01T10:00", "2026-12-01T11:00", freq="daily", interval=2, until=dt.date(2026, 12, 8))
    # dates: 1, 3, 5, 7 (Dec 9 would be past until). Last ends 7 Dec 11:00 IST = 05:30 UTC.
    assert ev.last_end_utc == dt.datetime(2026, 12, 7, 5, 30, tzinfo=UTC)
    assert ev.count is None and ev.until == dt.date(2026, 12, 8)
    assert [o[0].isoformat() for o in rec.expand(ev, *W("2026-12-07T00:00", "2026-12-08T00:00"))] == ["2026-12-07"]
    assert rec.expand(ev, *W("2026-12-07T05:30", "2026-12-09T00:00")) == []  # window starts at the end instant: nothing
    assert es.events_for(a, *W("2026-12-07T05:29", "2026-12-07T05:31")) != []
    assert es.events_for(a, *W("2026-12-07T05:30", "2026-12-20T00:00")) == []  # negative control: after the last end


@pytest.mark.django_db
def test_last_end_skips_a_final_date_inside_a_spring_forward_gap():
    a = _u("RE-2")
    # New York 2027-03-14 02:30 does not exist. Daily from 13 Mar, three dates: 13, 14 (gap), 15.
    ev = mk(a, "2027-03-13T02:30", "2027-03-13T03:15", tz=NY, freq="daily", count=2)
    assert rec.occurrence(ev, dt.date(2027, 3, 14)) is None
    shown = [d for d in rec.local_dates(ev) if rec.occurrence(ev, d)]
    assert shown == [dt.date(2027, 3, 13)]  # count 2 used one date on the gap
    assert ev.last_end_utc == rec.occurrence(ev, dt.date(2027, 3, 13))[1]


@pytest.mark.django_db
def test_series_overlap_uses_the_occurrence_not_the_first_day_and_mid_occurrence_counts():
    a = _u("RE-3")
    s = mk(a, "2026-12-01T10:00", "2026-12-01T11:00", freq="daily", count=5)
    other = mk(a, "2026-12-04T10:30", "2026-12-04T10:45")
    shown, total, _ = es.overlaps_for(a, other)
    assert total == 1 and shown[0].event.pk == s.pk  # overlaps the 4 Dec occurrence only
    edge = mk(a, "2026-12-04T11:00", "2026-12-04T12:00")  # starts exactly when the occurrence ends
    assert es.overlaps_for(a, edge)[1] == 0


@pytest.mark.django_db
def test_duration_is_kept_across_a_dst_change_in_the_event_zone():
    a = _u("RE-4")
    ev = mk(a, "2027-03-13T23:00", "2027-03-14T01:00", tz=NY, freq="daily", count=2)  # 2 h on the first night
    s2, e2 = rec.occurrence(ev, dt.date(2027, 3, 14))
    assert e2 - s2 == ev.end_utc - ev.start_utc  # elapsed duration, 2 h
    assert s2.astimezone(ZoneInfo(NY)).strftime("%H:%M") == "23:00"
    assert e2.astimezone(ZoneInfo(NY)).strftime("%H:%M") == "01:00"
