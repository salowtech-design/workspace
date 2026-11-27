import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.events import ics
from apps.events import recurrence as rec
from apps.events import services as es
from apps.events.models import Event, EventException, Guest
from tests.unit.test_events_views import _c, _u

D = dt.datetime
IST, NY, LON = "Asia/Kolkata", "America/New_York", "Europe/London"
NOW = D(2026, 11, 28, tzinfo=dt.timezone.utc)


def _rule(freq, count=None, until=None, weekdays=(), interval=1):
    return {"freq": freq, "interval": interval, "weekdays": list(weekdays), "count": count, "until": until}


def _build():
    a, b, other = _u("ICS-A"), _u("ICS-B"), _u("ICS-O")
    mk = es.create_event
    tricky = mk(a, "Plan, review; \\ and ünï\u00e7ode \u2603 " + "long " * 20, D(2026, 12, 7, 10), D(2026, 12, 7, 11), IST,
                location="Room 4, Block; B", description="Line one\nLine two, with comma; and a semicolon\r\nLine three " + "word " * 40)
    mk(a, "Offsite", D(2026, 12, 7), D(2026, 12, 9), IST, all_day=True)
    daily = mk(a, "Standup NY", D(2027, 3, 10, 10), D(2027, 3, 10, 10, 30), NY, rule=_rule("daily", count=6))
    EventException.objects.create(event=daily, local_date=dt.date(2027, 3, 12))
    mk(a, "Alt weeks", D(2027, 3, 22, 9), D(2027, 3, 22, 10), LON, rule=_rule("weekly", until=dt.date(2027, 4, 30), weekdays=[0, 2, 4], interval=2))
    mk(a, "Month end", D(2026, 12, 31, 18), D(2026, 12, 31, 19), IST, rule=_rule("monthly", count=5))
    mk(a, "Gap series", D(2027, 3, 13, 2, 30), D(2027, 3, 13, 3, 30), NY, rule=_rule("daily", count=3))
    inv = mk(other, "Invited to this", D(2026, 12, 8, 14), D(2026, 12, 8, 15), IST, guest_ids=[a.pk])
    dec = mk(other, "Declined one", D(2026, 12, 9, 14), D(2026, 12, 9, 15), IST, guest_ids=[a.pk])
    Guest.objects.filter(event=dec, person=a).update(response=Guest.DECLINED)
    gone = mk(a, "Cancelled one", D(2026, 12, 10, 14), D(2026, 12, 10, 15), IST)
    es.cancel_event(a, gone.public_id)
    mk(other, "Not mine", D(2026, 12, 11, 14), D(2026, 12, 11, 15), IST, guest_ids=[b.pk])
    Event.objects.create(organizer=a, title="Already ended", start_utc=NOW - dt.timedelta(days=3, hours=1),
                         end_utc=NOW - dt.timedelta(days=3), tz=IST)
    return a, b, tricky, daily, inv


def _unfold(text):
    return text.replace("\r\n ", "").split("\r\n")


@pytest.mark.django_db
def test_export_content_scope_and_format():
    a, b, tricky, daily, inv = _build()
    text, n = ics.export_events(a, NOW)
    assert text.endswith("END:VCALENDAR\r\n") and "\n" not in text.replace("\r\n", "")
    assert all(len(x.encode()) <= 75 for x in text.split("\r\n"))  # folded
    lines = _unfold(text)
    titles = [x for x in lines if x.startswith("SUMMARY:")]
    assert len(titles) == n == 7
    blob = "\n".join(lines)
    for gone in ("Declined one", "Cancelled one", "Not mine", "Already ended"):
        assert gone not in blob
    assert "Invited to this" in blob
    assert "ATTENDEE" not in blob and "ORGANIZER" not in blob and "ICS-B" not in blob and "ics-b@" not in blob
    assert [x for x in lines if x.startswith("SUMMARY:Plan")][0].startswith("SUMMARY:Plan\\, review\\; \\\\ and ünï")
    desc = [x for x in lines if x.startswith("DESCRIPTION:Line one")][0]
    assert desc.startswith("DESCRIPTION:Line one\\nLine two\\, with comma\\; and a semicolon\\nLine three ")
    assert "LOCATION:Room 4\\, Block\\; B" in lines
    assert "DTSTART;VALUE=DATE:20261207" in lines and "DTEND;VALUE=DATE:20261210" in lines
    assert f"DTSTART:{es.Event.objects.get(pk=tricky.pk).start_utc:%Y%m%dT%H%M%SZ}" in lines
    assert "RRULE:FREQ=DAILY;INTERVAL=1;COUNT=6" in lines
    assert "EXDATE;TZID=America/New_York:20270312T100000" in lines
    assert any(x.startswith("RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE,FR;WKST=MO;UNTIL=") for x in lines)
    assert "RRULE:FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=31;COUNT=5" in lines
    gap = [i for i, x in enumerate(lines) if x == "SUMMARY:Gap series"][0]
    seg = lines[:gap][-8:]
    assert not any(x.startswith("RRULE") for x in seg) and any(x.startswith("RDATE:") for x in seg)  # listed, not a rule
    assert lines.count("BEGIN:VTIMEZONE") == 3 and {"TZID:America/New_York", "TZID:Europe/London", "TZID:Asia/Kolkata"} <= set(lines)
    assert "TZNAME:EDT" in lines and "TZNAME:BST" in lines
    seq = [x for x in lines if x.startswith("UID:")]
    assert f"UID:{inv.public_id}@salow" in seq and len(set(seq)) == len(seq)


@pytest.mark.django_db
def test_view_download_login_and_headers_and_a_negative_control():
    a, b, *_ = _build()
    anon = _c(a).__class__(HTTP_HOST="app.salowtech.com")
    assert anon.get(reverse("events:export")).status_code == 302  # login needed
    r = _c(a).get(reverse("events:export"))
    assert r.status_code == 200 and r["Content-Type"].startswith("text/calendar")
    assert r["Content-Disposition"] == 'attachment; filename="salow-calendar.ics"' and "no-store" in r["Cache-Control"]
    assert b"Not mine" not in r.content
    rb = _c(b).get(reverse("events:export"))  # negative control: another person gets only their own invitation
    assert b"Not mine" in rb.content and b"Standup NY" not in rb.content and b"Offsite" not in rb.content
    home = _c(a).get(reverse("events:home"))
    assert reverse("events:export") in home.content.decode()


@pytest.mark.django_db
def test_too_many_events_is_refused_not_cut(monkeypatch):
    a, *_ = _build()
    monkeypatch.setattr(ics, "MAX_EVENTS", 3)
    with pytest.raises(ValidationError, match="too many for one file"):
        ics.export_events(a, NOW)
    r = _c(a).get(reverse("events:export"))
    assert r.status_code == 400 and b"Nothing was exported" in r.content


@pytest.mark.django_db
def test_dump_for_outside_parser_check():
    """Writes the file and the app's own occurrence list so a separate parser can compare them (only when ICS_OUT is set)."""
    out = os.environ.get("ICS_OUT")
    if not out:
        pytest.skip("ICS_OUT not set")
    a, *_ = _build()
    text, _n = ics.export_events(a, NOW)
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "salow.ics"), "w", newline="").write(text)
    exp = {}
    for ev in Event.objects.filter(organizer=a, status="confirmed").exclude(title="Already ended") | Event.objects.filter(title="Invited to this"):
        if ev.freq:
            sk = set(EventException.objects.filter(event=ev).values_list("local_date", flat=True))
            occ = [(rec.occurrence(ev, d)) for d in rec.local_dates(ev) if d not in sk]
            occ = [o for o in occ if o]
        else:
            occ = [(ev.start_utc, ev.end_utc)]
        exp[str(ev.public_id)] = {"title": ev.title, "all_day": ev.all_day, "tz": ev.tz,
                                  "occ": [[s.isoformat(), e.isoformat()] for s, e in occ]}
    json.dump(exp, open(os.path.join(out, "expected.json"), "w"), indent=1)
