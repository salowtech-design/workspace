import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.events import services as es
from apps.events.models import Event
from tests.unit.test_events_views import FORM, _c, _u

IST, NY = "Asia/Kolkata", "America/New_York"
D = dt.datetime


@pytest.mark.django_db
def test_service_stores_whole_local_days_end_exclusive_and_ignores_the_time_part():
    a = _u("AD-1")
    ev = es.create_event(a, "Offsite", D(2026, 12, 7, 15, 40), D(2026, 12, 9, 2, 5), IST, all_day=True)
    z = ZoneInfo(IST)
    assert ev.all_day is True
    assert ev.start_utc.astimezone(z) == D(2026, 12, 7, 0, 0, tzinfo=z)
    assert ev.end_utc.astimezone(z) == D(2026, 12, 10, 0, 0, tzinfo=z)  # last day 9 Dec, end is the midnight after
    one = es.create_event(a, "Holiday", D(2026, 12, 25, 9), D(2026, 12, 25, 9), IST, all_day=True)  # same date start and end
    assert (one.end_utc - one.start_utc) == dt.timedelta(days=1)
    timed = es.create_event(a, "Timed", D(2026, 12, 7, 15, 40), D(2026, 12, 7, 16, 40), IST)
    assert timed.all_day is False and (timed.end_utc - timed.start_utc) == dt.timedelta(hours=1)  # negative control


@pytest.mark.django_db
def test_all_day_refusals_and_a_dst_day_in_new_york():
    a = _u("AD-2")
    rule = {"freq": "daily", "interval": 1, "weekdays": [], "count": 3, "until": None}
    with pytest.raises(ValidationError):
        es.create_event(a, "X", D(2026, 12, 7), D(2026, 12, 7), IST, rule=rule, all_day=True)
    with pytest.raises(ValidationError):
        es.create_event(a, "X", D(2026, 12, 9), D(2026, 12, 7), IST, all_day=True)  # last day before first day
    with pytest.raises(ValidationError):
        es.create_event(a, "X", D(2026, 12, 1), D(2026, 12, 20), IST, all_day=True)  # 20 days is over the 14-day limit
    assert Event.objects.count() == 0
    ev = es.create_event(a, "Clocks change", D(2027, 3, 14), D(2027, 3, 14), NY, all_day=True)
    assert ev.end_utc - ev.start_utc == dt.timedelta(hours=23)  # a real 23-hour day, still local midnight to midnight
    z = ZoneInfo(NY)
    assert ev.end_utc.astimezone(z).strftime("%H:%M") == "00:00"


@pytest.mark.django_db
def test_edit_keeps_it_all_day_and_an_unchanged_save_changes_nothing():
    a = _u("AD-3")
    ev = es.create_event(a, "Offsite", D(2026, 12, 7), D(2026, 12, 8), IST, all_day=True)
    again = es.update_event(a, ev.public_id, 1, "Offsite", D(2026, 12, 7, 13), D(2026, 12, 8, 17), IST)  # times are ignored
    assert again.version == 1  # same days: no bump
    moved = es.update_event(a, ev.public_id, 1, "Offsite", D(2026, 12, 8, 1), D(2026, 12, 8, 1), IST)
    ev.refresh_from_db()
    assert moved.version == 2 and ev.all_day and (ev.end_utc - ev.start_utc) == dt.timedelta(days=1)


@pytest.mark.django_db
def test_pages_show_all_day_and_the_unticked_form_makes_a_timed_event():
    org = _u("AD-4")
    c = _c(org)
    r = c.post(reverse("events:new"), {**FORM, "title": "Away", "start": "2026-12-07T08:00", "end": "2026-12-09T08:00", "all_day": "on"})
    assert r.status_code == 302
    ev = Event.objects.get(title="Away")
    assert ev.all_day
    detail = c.get(reverse("events:detail", args=[ev.public_id])).content.decode()
    assert "Mon 7 Dec 2026 to Wed 9 Dec 2026, all day" in detail
    for name, q in (("day", "2026-12-08"), ("week", "2026-12-07")):
        assert "Mon 7 Dec to Wed 9 Dec, all day" in c.get(reverse(f"events:{name}") + f"?d={q}").content.decode()
    month = c.get(reverse("events:month") + "?m=2026-12").content.decode()
    assert month.count("All day") >= 3  # one chip on each of the three days
    edit = c.get(reverse("events:edit", args=[ev.public_id])).content.decode()
    assert 'value="2026-12-09T00:00"' in edit and "all-day event" in edit  # end shows the last day, not the exclusive end
    c.post(reverse("events:new"), {**FORM, "title": "Timed"})  # no all_day field
    t = Event.objects.get(title="Timed")
    assert t.all_day is False
    assert "all day" not in c.get(reverse("events:detail", args=[t.public_id])).content.decode()
    # repeating + all day is refused by the form with nothing saved
    r = c.post(reverse("events:new"), {**FORM, "title": "Rep", "all_day": "on", "freq": "daily", "count": "2"})
    assert r.status_code == 400 and not Event.objects.filter(title="Rep").exists()
