import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404
from django.urls import reverse

from apps.core.models import Notification
from apps.events import services as es
from tests.unit.test_chat import _c
from tests.unit.test_events import IST, D, _u

S, E = D(2026, 11, 27, 10), D(2026, 11, 27, 11)


def _ev(**kw):
    org, g, x = _u("LC-ORG"), _u("LC-G"), _u("LC-X")
    ev = es.create_event(org, "Review", S, E, IST, [g.pk], **kw)
    return org, g, x, ev


@pytest.mark.django_db
def test_create_stores_trimmed_location_and_description_and_defaults_empty():
    org, g, x, ev = _ev(location="  Room next to reception ", description="Bring the rota.\r\nSecond line")
    ev.refresh_from_db()
    assert ev.location == "Room next to reception" and ev.description == "Bring the rota.\nSecond line"
    plain = es.create_event(org, "Plain", S, E, IST, [])
    assert (plain.location, plain.description) == ("", "")


@pytest.mark.django_db
def test_limits_and_control_characters_are_refused():
    org = _u("LC-O2")
    for kw in (
        {"location": "x" * 201},
        {"location": "line1\nline2"},
        {"location": "tab\there"},
        {"location": "nul\x00"},
        {"location": "sep\u2028x"},
        {"description": "y" * 2001},
        {"description": "bell\x07"},
        {"description": "n\x85el"},
    ):
        with pytest.raises(ValidationError):
            es.create_event(org, "T", S, E, IST, [], **kw)
    es.create_event(org, "T", S, E, IST, [], location="x" * 200, description="y" * 2000)  # the exact limits are allowed


@pytest.mark.django_db
def test_organiser_edit_changes_them_bumps_version_and_old_version_conflicts():
    org, g, x, ev = _ev(location="A", description="one")
    v = ev.version
    new = es.update_event(org, ev.public_id, v, "Review", S, E, IST, location="B", description="two")
    assert (new.location, new.description, new.version) == ("B", "two", v + 1)
    with pytest.raises(es.StaleEvent):
        es.update_event(org, ev.public_id, v, "Review", S, E, IST, location="C", description="two")  # stale form
    ev.refresh_from_db()
    assert ev.location == "B"
    same = es.update_event(org, ev.public_id, ev.version, "Review", S, E, IST, location="B", description="two")
    assert same.version == ev.version  # nothing changed: no bump


@pytest.mark.django_db
def test_update_without_the_fields_leaves_them_alone_and_blank_clears_them():
    org, g, x, ev = _ev(location="Keep", description="Keep too")
    es.update_event(org, ev.public_id, ev.version, "Renamed", S, E, IST)  # an older caller that sends neither
    ev.refresh_from_db()
    assert (ev.title, ev.location, ev.description) == ("Renamed", "Keep", "Keep too")
    es.update_event(org, ev.public_id, ev.version, "Renamed", S, E, IST, location="", description="")
    ev.refresh_from_db()
    assert (ev.location, ev.description) == ("", "")


@pytest.mark.django_db
def test_guest_and_stranger_cannot_edit_and_stranger_cannot_see_the_text():
    org, g, x, ev = _ev(location="Secret room", description="Secret agenda")
    for who in (g, x):
        with pytest.raises(Http404):
            es.update_event(who, ev.public_id, ev.version, "Review", S, E, IST, location="hack", description="hack")
    ev.refresh_from_db()
    assert ev.location == "Secret room"
    url = reverse("events:detail", args=[ev.public_id])
    assert b"Secret agenda" in _c(g).get(url).content  # a guest sees it, like the rest of the event
    assert b"Secret agenda" in _c(org).get(url).content
    r = _c(x).get(url)
    assert r.status_code == 404 and b"Secret" not in r.content


@pytest.mark.django_db
def test_text_is_escaped_on_detail_and_form():
    org, g, x, ev = _ev(location="<script>alert(1)</script>", description="<img src=x onerror=alert(2)>\nnext")
    h = _c(g).get(reverse("events:detail", args=[ev.public_id])).content.decode()
    assert "<script>alert(1)</script>" not in h and "&lt;script&gt;alert(1)&lt;/script&gt;" in h
    assert "<img src=x" not in h and "&lt;img src=x onerror=alert(2)&gt;<br>next" in h
    f = _c(org).get(reverse("events:edit", args=[ev.public_id])).content.decode()
    assert "<script>alert(1)</script>" not in f and "&lt;script&gt;" in f


@pytest.mark.django_db
def test_form_roundtrip_through_the_views_and_validation_error_keeps_the_page():
    org = _u("LC-V")
    c = _c(org)
    r = c.post(reverse("events:new"), {"title": "T", "start": "2026-11-27T10:00", "end": "2026-11-27T11:00", "tz": IST,
                                       "location": "Office 2", "description": "Agenda"})
    assert r.status_code == 302
    ev = es.Event.objects.get(title="T")
    assert (ev.location, ev.description) == ("Office 2", "Agenda")
    bad = c.post(reverse("events:edit", args=[ev.public_id]), {"version": ev.version, "title": "T", "start": "2026-11-27T10:00",
                                                               "end": "2026-11-27T11:00", "tz": IST, "location": "x" * 201, "description": ""})
    assert bad.status_code == 400
    ev.refresh_from_db()
    assert ev.location == "Office 2"
    ok = c.post(reverse("events:edit", args=[ev.public_id]), {"version": ev.version, "title": "T", "start": "2026-11-27T10:00",
                                                              "end": "2026-11-27T11:00", "tz": IST, "location": "Office 3", "description": "Agenda"})
    assert ok.status_code == 302
    ev.refresh_from_db()
    assert ev.location == "Office 3"


@pytest.mark.django_db
def test_repeating_series_and_cancelled_events_follow_existing_rules():
    org = _u("LC-R")
    rule = {"freq": "daily", "interval": 1, "weekdays": [], "count": 3, "until": None}
    ev = es.create_event(org, "Daily", S, E, IST, [], rule=rule, location="Hall")
    new = es.update_event(org, ev.public_id, ev.version, "Daily", S, E, IST, location="Hall B", description="d")
    assert new.location == "Hall B" and new.freq == "daily"
    es.cancel_event(org, ev.public_id)
    with pytest.raises(ValidationError):
        es.update_event(org, ev.public_id, new.version + 1, "Daily", S, E, IST, location="after cancel", description="d")


@pytest.mark.django_db
def test_invite_notice_does_not_carry_location_or_description():
    org, g, x, ev = _ev(location="Vault room", description="Budget cuts")
    n = Notification.objects.get(user=g, kind="events.invited")
    assert "Vault" not in n.title and "Budget" not in n.title and "Vault" not in n.link
