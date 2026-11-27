import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
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
    assert 'type="date"' in edit and 'value="2026-12-09"' in edit and "all-day event" in edit  # date boxes, end shows the last day not the exclusive end
    assert 'value="2026-12-07"' in edit and "datetime-local" not in edit
    c.post(reverse("events:new"), {**FORM, "title": "Dates only", "start": "2026-12-14", "end": "2026-12-15", "all_day": "on"})  # date-only boxes
    assert Event.objects.get(title="Dates only").all_day
    bad = c.post(reverse("events:new"), {**FORM, "title": "No day", "start": "", "end": "", "all_day": "on"})
    assert bad.status_code == 400 and 'type="date"' in bad.content.decode() and not Event.objects.filter(title="No day").exists()  # re-shown as date boxes
    timed_bad = c.post(reverse("events:new"), {**FORM, "title": "Timed bad", "start": "2026-12-14", "end": "2026-12-15"})  # date only, but not all day
    assert timed_bad.status_code == 400 and not Event.objects.filter(title="Timed bad").exists()  # negative control
    new_page = c.get(reverse("events:new")).content.decode()
    assert 'type="datetime-local"' in new_page and "First day" not in new_page
    c.post(reverse("events:new"), {**FORM, "title": "Timed"})  # no all_day field
    t = Event.objects.get(title="Timed")
    assert t.all_day is False
    assert "all day" not in c.get(reverse("events:detail", args=[t.public_id])).content.decode()
    # repeating + all day is refused by the form with nothing saved
    r = c.post(reverse("events:new"), {**FORM, "title": "Rep", "all_day": "on", "freq": "daily", "count": "2"})
    assert r.status_code == 400 and not Event.objects.filter(title="Rep").exists()


@pytest.mark.django_db
def test_the_fourteen_day_limit_counts_local_days_so_a_clock_change_does_not_move_it():
    a = _u("AD-5")
    # New York falls back on 1 Nov 2026 (that day has 25 hours). 25 Oct to 7 Nov is 14 local days = 14 days + 1 hour elapsed.
    ev = es.create_event(a, "Fall back span", D(2026, 10, 25), D(2026, 11, 7), NY, all_day=True)
    assert ev.end_utc - ev.start_utc == dt.timedelta(days=14, hours=1)  # over the elapsed 14 days, still accepted
    with pytest.raises(ValidationError):
        es.create_event(a, "15 days", D(2026, 10, 25), D(2026, 11, 8), NY, all_day=True)  # 15 local days
    # spring forward (14 Mar 2027 has 23 hours): 14 days accepted, 15 refused, the same answer
    es.create_event(a, "Spring span", D(2027, 3, 7), D(2027, 3, 20), NY, all_day=True)
    with pytest.raises(ValidationError):
        es.create_event(a, "Spring 15", D(2027, 3, 7), D(2027, 3, 21), NY, all_day=True)
    # negative control: a TIMED event still uses elapsed time (14 days + 1 hour is refused)
    with pytest.raises(ValidationError):
        es.create_event(a, "Timed long", D(2026, 10, 25, 0), D(2026, 11, 8, 1), NY)
    assert Event.objects.count() == 2


@pytest.mark.django_db
def test_a_day_whose_midnight_does_not_exist_is_refused_with_its_own_message():
    a = _u("AD-6")
    z = "America/Havana"  # midnight on 14 Mar 2027 does not exist (the clocks jump 00:00 to 01:00)
    with pytest.raises(ValidationError) as ex:
        es.create_event(a, "Gap start", D(2027, 3, 14), D(2027, 3, 14), z, all_day=True)
    assert "clock change removes midnight" in " ".join(ex.value.messages)
    with pytest.raises(ValidationError):
        es.create_event(a, "Gap end", D(2027, 3, 13), D(2027, 3, 13), z, all_day=True)  # its end midnight is the missing one
    ok = es.create_event(a, "Day before", D(2027, 3, 12), D(2027, 3, 12), z, all_day=True)
    assert ok.all_day
    with pytest.raises(ValidationError) as ex2:
        es.create_event(a, "Timed gap", D(2027, 3, 14, 0, 30), D(2027, 3, 14, 2), z)
    assert "does not exist in the time zone" in " ".join(ex2.value.messages)  # the timed message is unchanged


@pytest.mark.django_db
def test_a_viewer_in_another_zone_sees_the_organisers_dates_not_converted_ones():
    org, guest = _u("AD-7"), _u("AD-8")
    # Kiritimati (UTC+14) midnight of 7 Dec is 6 Dec 15:30 in Kolkata: a converted display would say 6 Dec.
    far = es.create_event(org, "Far east day", D(2026, 12, 7), D(2026, 12, 7), "Pacific/Kiritimati", [guest.pk], all_day=True)
    ny = es.create_event(org, "New York days", D(2026, 12, 7), D(2026, 12, 8), NY, [guest.pk], all_day=True)
    c = _c(guest)  # the viewer's pages are in Asia/Kolkata
    d_far = c.get(reverse("events:detail", args=[far.public_id])).content.decode()
    assert "Mon 7 Dec 2026, all day (Pacific/Kiritimati)" in d_far and "6 Dec" not in d_far
    d_ny = c.get(reverse("events:detail", args=[ny.public_id])).content.decode()
    assert "Mon 7 Dec 2026 to Tue 8 Dec 2026, all day (America/New_York)" in d_ny
    day7 = c.get(reverse("events:day") + "?d=2026-12-07").content.decode()
    day6 = c.get(reverse("events:day") + "?d=2026-12-06").content.decode()
    assert "Far east day" in day7 and "Far east day" not in day6  # on the organiser's date, not the viewer's converted date
    assert "Mon 7 Dec, all day" in day7 and "New York days" in day7
    month = c.get(reverse("events:month") + "?m=2026-12").content.decode()
    assert month.count("Far east day") >= 1 and "Mon 7 Dec to Tue 8 Dec, all day" in month
    # the agenda: an event a few days from now
    soon = timezone.now().astimezone(ZoneInfo(NY)).date() + dt.timedelta(days=3)
    s = es.create_event(org, "Agenda day", D(soon.year, soon.month, soon.day), D(soon.year, soon.month, soon.day), NY, [guest.pk], all_day=True)
    home = c.get(reverse("events:home")).content.decode()
    assert f"{soon.day} " in home and "all day (America/New_York)" in home
    assert Event.objects.get(pk=s.pk).all_day


@pytest.mark.django_db
def test_error_and_stale_rerenders_keep_the_typed_dates_and_do_not_add_a_day():
    org = _u("AD-9")
    c = _c(org)
    ev = es.create_event(org, "Away", D(2026, 12, 7), D(2026, 12, 9), IST, all_day=True)
    page = c.get(reverse("events:edit", args=[ev.public_id])).content.decode()
    assert 'name="end" id="cal-end" value="2026-12-09"' in page
    bad = c.post(reverse("events:edit", args=[ev.public_id]), {"title": "", "start": "2026-12-07", "end": "2026-12-09", "tz": IST, "version": "1"})
    assert bad.status_code == 400 and 'id="cal-end" value="2026-12-09"' in bad.content.decode()  # error: still the last day
    es.update_event(org, ev.public_id, 1, "Away", D(2026, 12, 7), D(2026, 12, 9), IST, location="Room 1")  # someone else saves first (v2)
    stale = c.post(reverse("events:edit", args=[ev.public_id]), {"title": "Away 2", "start": "2026-12-07", "end": "2026-12-09", "tz": IST, "version": "1"})
    assert stale.status_code == 409 and 'id="cal-end" value="2026-12-09"' in stale.content.decode()
    assert 'id="cal-start" value="2026-12-07"' in stale.content.decode()
    # saving the shown dates with the fresh version moves nothing: no extra day (the title change is the only change)
    ok = c.post(reverse("events:edit", args=[ev.public_id]), {"title": "Away 2", "start": "2026-12-07", "end": "2026-12-09", "tz": IST, "version": "2"})
    assert ok.status_code == 302
    ev.refresh_from_db()
    assert ev.version == 3 and ev.title == "Away 2" and ev.end_utc.astimezone(ZoneInfo(IST)).date() == dt.date(2026, 12, 10)
    same = c.post(reverse("events:edit", args=[ev.public_id]), {"title": "Away 2", "start": "2026-12-07", "end": "2026-12-09", "tz": IST, "version": "3"})
    ev.refresh_from_db()
    assert same.status_code == 302 and ev.version == 3  # nothing changed: no bump, no drift
    # a new all-day form typed with date and time boxes (no script) is shown again as dates, and a bad one is not saved
    new_bad = c.post(reverse("events:new"), {**FORM, "title": "", "start": "2026-12-03T15:40", "end": "2026-12-05T02:10", "all_day": "on"}).content.decode()
    assert 'id="cal-start" value="2026-12-03"' in new_bad and 'id="cal-end" value="2026-12-05"' in new_bad and "15:40" not in new_bad
