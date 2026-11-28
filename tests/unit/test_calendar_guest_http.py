"""Calendar guest and non-guest access over HTTP, with real events and a full-row before/after snapshot (every field of
every row outside throttle, Django-session and verification-log tables; core.auditlog IS included, so denied actions also assert no audit row; last_login and last_activity stripped).

The rules tested are the ones apps/events/services.py labels as build defaults, not user policy: guest visibility is pinned only as
guest-sees-guest; only the organiser edits or cancels; any signed-in active person may read free/busy (busy
intervals only, no titles, no guests). This test pins today's behaviour for those defaults; it does not decide them.
Event: organiser O, guests G1 and G2, one outsider X, dated 2026-12-03 10:00 to 11:00 Asia/Kolkata, with a marker in the
title and description.
  - outsider X: detail and edit answer 404; a valid edit, a valid cancel and a respond change no row; the day, week and
    month pages and the .ics export carry no marker; free/busy for O shows a busy interval and no marker;
  - guest G1: sees the event (control, detail and day page and .ics carry the marker); G1 sees G2 on the page;
    a valid edit and a cancel change no row; respond to accepted changes only G1's own guest row plus one audit row;
  - organiser O, the SAME edit and cancel payloads: the title becomes the new title and the status becomes cancelled, so the
    payloads are valid and the guest's no-change was a refusal.
Limits: single events only (no series, skip, or all-day); guest add and remove after create is a held decision and is not
touched; test client without CSRF; one machine, one run; not an authorization proof."""

import datetime as dt

import pytest
from django.apps import apps as django_apps
from django.test import Client

from apps.accounts.models import CustomUser
from apps.events import services as es
from apps.events.models import Event, Guest

HOST = "app.salowtech.com"
MARK = "ZqCalMarker"
EXCLUDED = {"accounts.authevent", "accounts.loginattempt", "core.ratelimitbucket", "core.ratelimitrecord", "sessions.session",
            "certificates.certificateverificationlog", "face.faceverificationlog"}  # fmt: skip


def _u(eid):
    return CustomUser.objects.create(employee_id=eid, full_name=f"{eid} Person", email=f"{eid.lower()}@salowtech.com",
                                     role="employee", status="active", is_active=True)  # fmt: skip


def _snapshot():
    snap = {}
    for m in django_apps.get_models():
        label = m._meta.label_lower
        if m._meta.proxy or label in EXCLUDED:
            continue
        for row in m.objects.all().values():
            row.pop("last_login", None)
            row.pop("last_activity", None)
            snap[(label, str(row.get("id", row.get("pk", row))))] = repr(sorted(row.items(), key=lambda kv: kv[0]))
    return snap


def _changed(before, after):
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


@pytest.fixture
def world(db):
    o, g1, g2, x = _u("CG-O"), _u("CG-G1"), _u("CG-G2"), _u("CG-X")
    ev = es.create_event(o, f"{MARK} review", dt.datetime(2026, 12, 3, 10), dt.datetime(2026, 12, 3, 11), "Asia/Kolkata",
                         [g1.pk, g2.pk], description=f"{MARK} description")  # fmt: skip
    return {"o": o, "g1": g1, "g2": g2, "x": x, "ev": ev}


def _client(user, n):
    c = Client(HTTP_HOST=HOST, REMOTE_ADDR=f"10.3.0.{n}")
    c.force_login(user)
    return c


def _urls(ev):
    base = f"/calendar/event/{ev.public_id}"
    return {"detail": base + "/", "edit": base + "/edit/", "cancel": base + "/cancel/", "respond": base + "/respond/"}


def _edit_payload(ev):
    return {"version": ev.version, "title": "ZqCalHijack", "start": "2026-12-03T10:00", "end": "2026-12-03T11:00",
            "tz": "Asia/Kolkata", "location": "", "description": f"{MARK} description"}  # fmt: skip


def _marker_in_pages(client):
    pages = ["/calendar/day/?d=2026-12-03", "/calendar/week/?d=2026-12-03", "/calendar/month/?m=2026-12", "/calendar/export.ics"]
    return {p: MARK.encode() in client.get(p).content for p in pages}


def test_outsider_cannot_see_or_change_the_event(world):
    w, u = world, _urls(world["ev"])
    c = _client(w["x"], 1)
    assert c.get(u["detail"]).status_code == 404 and c.get(u["edit"]).status_code == 404
    assert not any(_marker_in_pages(c).values()), _marker_in_pages(c)
    busy = c.get("/calendar/busy/?date=2026-12-03&people=CG-O")
    assert busy.status_code == 200 and MARK.encode() not in busy.content, "free/busy must not show the title"
    assert b"10:00 to 11:00" in busy.content, "free/busy (build default) should show the exact busy interval"
    g2busy = c.get("/calendar/busy/?date=2026-12-03&people=CG-G2%2CCG-X")
    assert b"10:00 to 11:00" in g2busy.content, "control: the guest G2 is busy at the same time"
    free = c.get("/calendar/busy/?date=2026-12-04&people=CG-O")
    assert b"Free all day" in free.content and b"10:00 to 11:00" not in free.content, "control: a day with no event is free"
    for n, (key, data) in enumerate((("edit", _edit_payload(w["ev"])), ("cancel", {"confirm": "yes"}),
                                     ("respond", {"response": Guest.ACCEPTED})), start=2):  # fmt: skip
        before = _snapshot()
        r = _client(w["x"], n).post(u[key], data)
        assert r.status_code < 500
        assert not _changed(before, _snapshot()), f"outsider changed rows via {key} (status {r.status_code})"


def test_guest_sees_the_event_and_the_other_guest_but_cannot_edit_or_cancel(world):
    w, u = world, _urls(world["ev"])
    c = _client(w["g1"], 1)
    page = c.get(u["detail"])
    assert page.status_code == 200 and MARK.encode() in page.content, "control: guest sees the event"
    assert b"CG-G2" in page.content, "guest sees the other guest (build default)"
    assert all(_marker_in_pages(c).values()), _marker_in_pages(c)
    for n, (key, data) in enumerate((("edit", _edit_payload(w["ev"])), ("cancel", {"confirm": "yes"})), start=2):
        before = _snapshot()
        r = _client(w["g1"], n).post(u[key], data)
        assert r.status_code < 500
        assert not _changed(before, _snapshot()), f"guest changed rows via {key} (status {r.status_code})"
    before = _snapshot()
    _client(w["g1"], 5).post(u["respond"], {"response": Guest.ACCEPTED})
    changed = _changed(before, _snapshot())
    assert {l for l, _pk in changed} == {"events.guest", "core.auditlog"}, changed
    assert [pk for l, pk in changed if l == "events.guest"] == [str(Guest.objects.get(event=w["ev"], person=w["g1"]).pk)], changed
    assert Guest.objects.get(event=w["ev"], person=w["g1"]).response == Guest.ACCEPTED
    assert Guest.objects.get(event=w["ev"], person=w["g2"]).response == Guest.NEEDS_ACTION


def test_organiser_with_the_same_payloads_edits_and_cancels(world):
    w, u = world, _urls(world["ev"])
    _client(w["o"], 1).post(u["edit"], _edit_payload(w["ev"]))
    w["ev"].refresh_from_db()
    assert w["ev"].title == "ZqCalHijack", "organiser edit payload did not set the title"
    _client(w["o"], 2).post(u["cancel"], {"confirm": "yes"})
    w["ev"].refresh_from_db()
    assert w["ev"].status == Event.CANCELLED, "organiser cancel payload did not cancel"
