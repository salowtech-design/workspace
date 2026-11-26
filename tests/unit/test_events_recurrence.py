import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.events import recurrence as rec
from apps.events import services as es
from apps.events.models import Event, EventException, Guest

UTC = dt.timezone.utc


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"P {eid}", email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip


def W(a, b):
    return dt.datetime.fromisoformat(a).replace(tzinfo=UTC), dt.datetime.fromisoformat(b).replace(tzinfo=UTC)


def mk(org, start, end, tz="Asia/Kolkata", guests=(), **rule):
    return es.create_event(org, "Series", dt.datetime.fromisoformat(start),
                           dt.datetime.fromisoformat(end), tz, [g.pk for g in guests],
                           rule=rule or None)  # fmt: skip


def days(occ):
    return [o.start_utc.astimezone(ZoneInfo(o.tz)).date().isoformat() for o in occ]


@pytest.mark.django_db
def test_old_weekly_series_is_found_by_a_later_window_and_ended_ones_are_not():
    org = _u("R1")
    ev = mk(org, "2026-09-01T10:00", "2026-09-01T11:00", freq="weekly", count=40)  # a Tuesday
    assert ev.last_end_utc is not None
    win = W("2026-12-01T00:00", "2026-12-31T00:00")  # 90 days after the first occurrence
    got = days(es.events_for(org, *win))
    assert got == ["2026-12-01", "2026-12-08", "2026-12-15", "2026-12-22", "2026-12-29"]
    # an edge-crossing occurrence: the window starts inside the 10:00 to 11:00 IST slot
    w2 = W("2026-12-01T05:00", "2026-12-02T00:00")  # 10:30 IST
    assert days(es.events_for(org, *w2)) == ["2026-12-01"]
    # ends before the window begins
    w3 = W("2026-12-01T05:30", "2026-12-02T00:00")  # exactly 11:00 IST, the end instant
    assert es.events_for(org, *w3) == []
    # a short series that has ended is not returned
    mk(org, "2026-09-02T10:00", "2026-09-02T11:00", freq="daily", count=3)
    assert len(es.events_for(org, *win)) == 5


@pytest.mark.django_db
def test_negative_control_first_occurrence_only_query_loses_the_old_series(monkeypatch):
    org = _u("R2")
    mk(org, "2026-09-01T10:00", "2026-09-01T11:00", freq="weekly", count=40)
    win = W("2026-12-01T00:00", "2026-12-31T00:00")
    assert es.events_for(org, *win)
    real = es._series_rows

    def first_only(flt, start, end):  # the naive query: the event's own start and end only
        singles, _ = real(flt, start, end)
        return list(Event.objects.filter(flt, start_utc__lt=end, end_utc__gt=start)), []

    monkeypatch.setattr(es, "_series_rows", first_only)
    assert es.events_for(org, *win) == []  # the row would fail if the real query regressed


@pytest.mark.django_db
def test_count_until_monthly_skip_and_local_until_inclusive():
    org = _u("R3")
    m = mk(org, "2026-01-31T10:00", "2026-01-31T11:00", freq="monthly", count=4)
    assert [d.isoformat() for d in rec.local_dates(m)] == ["2026-01-31", "2026-03-31", "2026-05-31", "2026-07-31"]
    # until is a local date, inclusive: 23:30 London on the until day still counts
    ev = mk(org, "2026-06-01T23:30", "2026-06-02T00:00", tz="Europe/London", freq="daily",
            until=dt.date(2026, 6, 3))  # fmt: skip
    occ = es.events_for(org, *W("2026-06-01T00:00", "2026-06-08T00:00"))
    got = [o for o in occ if o.event.pk == ev.pk]
    assert len(got) == 3  # 1, 2, 3 June local (22:30 UTC each)
    assert got[-1].start_utc == dt.datetime(2026, 6, 3, 22, 30, tzinfo=UTC)


@pytest.mark.django_db
def test_dst_gap_and_fold_london_and_count_semantics():
    org = _u("R4")
    # spring forward 2026-03-29: 01:30 does not exist in London that day
    gap = mk(org, "2026-03-27T01:30", "2026-03-27T02:00", tz="Europe/London", freq="daily", count=4)
    shown = days([o for o in es.events_for(org, *W("2026-03-26T00:00", "2026-04-05T00:00")) if o.event.pk == gap.pk])
    assert shown == ["2026-03-27", "2026-03-28", "2026-03-30"]  # the 29th is skipped, and used a count
    # fall back 2026-10-25: 01:30 happens twice; the first one (BST, 00:30 UTC) is used
    fold = mk(org, "2026-10-24T01:30", "2026-10-24T02:00", tz="Europe/London", freq="daily", count=3)
    occ = [o for o in es.events_for(org, *W("2026-10-24T00:00", "2026-10-28T00:00")) if o.event.pk == fold.pk]
    assert occ[1].start_utc == dt.datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    # a 10:00 slot stays 10:00 local across the change
    wall = mk(org, "2026-03-27T10:00", "2026-03-27T11:00", tz="Europe/London", freq="daily", count=4)
    occ = [o for o in es.events_for(org, *W("2026-03-26T00:00", "2026-04-05T00:00")) if o.event.pk == wall.pk]
    assert [o.start_utc.hour for o in occ] == [10, 10, 9, 9]


@pytest.mark.django_db
def test_weekly_weekday_is_local_not_utc():
    org = _u("R5")
    # Tue 01:00 IST is Mon 19:30 UTC: weekday 1 (Tuesday) is the local one
    ev = mk(org, "2026-12-01T01:00", "2026-12-01T01:30", freq="weekly", weekdays=[1, 4], count=4)
    assert [d.isoformat() for d in rec.local_dates(ev)] == ["2026-12-01", "2026-12-04", "2026-12-08", "2026-12-11"]
    with pytest.raises(ValidationError):  # the first day (Tuesday) must be in the set
        mk(org, "2026-12-01T01:00", "2026-12-01T01:30", freq="weekly", weekdays=[2], count=4)


@pytest.mark.django_db
def test_rule_bounds_are_checked_on_the_server():
    org = _u("R6")
    base = ("2026-12-01T10:00", "2026-12-01T11:00")
    bad = [
        dict(freq="yearly", count=3), dict(freq="daily"), dict(freq="daily", count=3, until=dt.date(2026, 12, 9)),
        dict(freq="daily", count=0), dict(freq="daily", count=201), dict(freq="daily", count=3, interval=13),
        dict(freq="daily", count=3, interval=0), dict(freq="daily", count="x"),
        dict(freq="daily", until=dt.date(2026, 11, 1)), dict(freq="daily", until=dt.date(2029, 1, 1)),
        dict(freq="weekly", weekdays=[9], count=2),
    ]  # fmt: skip
    for r in bad:
        with pytest.raises(ValidationError):
            mk(org, *base, **r)
    with pytest.raises(ValidationError):  # longer than 24 hours
        mk(org, "2026-12-01T10:00", "2026-12-02T11:00", freq="daily", count=2)
    assert Event.objects.count() == 0
    one = mk(org, *base, freq="", )
    assert one.freq == "" and one.last_end_utc is None  # single events are unchanged


@pytest.mark.django_db
def test_skip_authorisation_real_date_duplicates_and_busy():
    org, g, out = _u("R7"), _u("R8"), _u("R9")
    ev = mk(org, "2026-12-01T10:00", "2026-12-01T11:00", guests=[g], freq="daily", count=5)
    d = dt.date(2026, 12, 3)
    for who in (g, out):
        with pytest.raises(Http404):
            es.skip_occurrence(who, ev.public_id, d)
    with pytest.raises(ValidationError):
        es.skip_occurrence(org, ev.public_id, dt.date(2026, 12, 9))  # not an occurrence
    es.skip_occurrence(org, ev.public_id, d)
    v1 = Event.objects.get(pk=ev.pk).version
    es.skip_occurrence(org, ev.public_id, d)  # again: no error, no second row, no version bump
    assert EventException.objects.count() == 1 and Event.objects.get(pk=ev.pk).version == v1
    win = W("2026-12-01T00:00", "2026-12-10T00:00")
    assert "2026-12-03" not in days(es.events_for(g, *win)) and len(es.events_for(g, *win)) == 4
    busy = es.free_busy(out, [g.pk], *win)[g.pk]
    assert len(busy) == 4  # each occurrence is busy, the skipped one is free
    assert not any(b[0].astimezone(ZoneInfo("Asia/Kolkata")).date() == d for b in busy)
    # guest decline removes the whole series from the guest
    Guest.objects.filter(person=g).update(response="declined")
    assert es.events_for(g, *win) == [] and es.free_busy(out, [g.pk], *win)[g.pk] == []
    # cancelling the series removes every occurrence
    es.cancel_event(org, ev.public_id)
    assert es.events_for(org, *win) == []


@pytest.mark.django_db
def test_edit_series_keeps_rule_and_refuses_date_move_when_occurrences_are_cancelled():
    org = _u("R10")
    ev = mk(org, "2026-12-01T10:00", "2026-12-01T11:00", freq="daily", count=5)
    ver = ev.version
    e2 = es.update_event(org, ev.public_id, ver, "Renamed", dt.datetime(2026, 12, 1, 14), dt.datetime(2026, 12, 1, 15), "Asia/Kolkata")
    assert e2.freq == "daily" and e2.count == 5 and e2.title == "Renamed"
    occ = es.events_for(org, *W("2026-12-01T00:00", "2026-12-10T00:00"))
    assert len(occ) == 5 and occ[0].start_utc.hour == 8 and occ[0].start_utc.minute == 30
    es.skip_occurrence(org, ev.public_id, dt.date(2026, 12, 2))
    cur = Event.objects.get(pk=ev.pk).version
    with pytest.raises(ValidationError):  # moving the first day would shift the cancelled date
        es.update_event(org, ev.public_id, cur, "Renamed", dt.datetime(2026, 12, 2, 14), dt.datetime(2026, 12, 2, 15), "Asia/Kolkata")
    es.update_event(org, ev.public_id, cur, "Renamed", dt.datetime(2026, 12, 1, 16), dt.datetime(2026, 12, 1, 17), "Asia/Kolkata")


@pytest.mark.django_db
def test_events_for_does_not_hand_out_the_saved_row_times():
    org = _u("R11")
    ev = mk(org, "2026-12-01T10:00", "2026-12-01T11:00", freq="daily", count=3)
    occ = es.events_for(org, *W("2026-12-01T00:00", "2026-12-10T00:00"))
    assert occ[2].start_utc != ev.start_utc and occ[2].event.start_utc == ev.start_utc
    assert Event.objects.get(pk=ev.pk).start_utc == ev.start_utc


@pytest.mark.django_db
def test_views_create_series_show_it_everywhere_and_skip_by_organiser_only():
    org, g, out = _u("V1"), _u("V2"), _u("V3")
    co = Client(HTTP_HOST="app.salowtech.com"); co.force_login(org)
    cg = Client(HTTP_HOST="app.salowtech.com"); cg.force_login(g)
    cx = Client(HTTP_HOST="app.salowtech.com"); cx.force_login(out)
    r = co.post(reverse("events:new"), {"title": "Daily", "start": "2026-12-01T10:00", "end": "2026-12-01T11:00",
                                        "tz": "Asia/Kolkata", "guests": "v2", "freq": "daily", "count": "4"})  # fmt: skip
    assert r.status_code == 302
    ev = Event.objects.get()
    month = cg.get(reverse("events:month"), {"m": "2026-12"})
    cells = {d["date"].isoformat(): d for w in month.context["weeks"] for d in w}
    assert all(any(x["e"].title == "Daily" for x in cells[f"2026-12-0{i}"]["shown"]) for i in (1, 2, 3, 4))
    assert not cells["2026-12-05"]["shown"]
    assert b"Repeats every day" in cg.get(reverse("events:detail", args=[ev.public_id])).content
    url = reverse("events:skip", args=[ev.public_id])
    assert cg.post(url, {"date": "2026-12-02"}).status_code == 404
    assert cx.post(url, {"date": "2026-12-02"}).status_code == 404
    assert EventException.objects.count() == 0
    co.post(url, {"date": "2026-12-02"})
    co.post(url, {"date": "garbage"})
    assert EventException.objects.count() == 1
    assert co.get(url).status_code == 405
    for bad in ({"freq": "daily"}, {"freq": "daily", "count": "3", "until": "2026-12-09"}, {"freq": "weird", "count": "2"}):
        r = co.post(reverse("events:new"), {"title": "X", "start": "2026-12-01T10:00", "end": "2026-12-01T11:00", "tz": "Asia/Kolkata", **bad})  # fmt: skip
        assert r.status_code == 400
    assert Event.objects.count() == 1
