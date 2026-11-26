import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.events.models import Event, Guest

HOST = "app.salowtech.com"


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip


def _c(user):
    c = Client(HTTP_HOST=HOST)
    c.force_login(user)
    return c


FORM = {
    "title": "Review",
    "start": "2026-12-01T10:00",
    "end": "2026-12-01T11:00",
    "tz": "Asia/Kolkata",
}


@pytest.mark.django_db
def test_create_through_the_form_and_detail_agenda_respond_cancel():
    org, g, out = _u("V-ORG"), _u("V-G"), _u("V-OUT")
    co, cg, cx = _c(org), _c(g), _c(out)
    r = co.post(reverse("events:new"), {**FORM, "guests": "v-g"})  # lower case ID is accepted
    assert r.status_code == 302
    ev = Event.objects.get()
    url = reverse("events:detail", args=[ev.public_id])
    assert Guest.objects.get().person_id == g.pk
    assert cg.get(url).status_code == 200 and co.get(url).status_code == 200
    assert cx.get(url).status_code == 404  # a stranger learns nothing
    for name in ("edit", "cancel"):
        u = reverse(f"events:{name}", args=[ev.public_id])
        assert cg.get(u).status_code == 404 and cx.get(u).status_code == 404
        assert cg.post(u, {"confirm": "yes", **FORM, "version": 1}).status_code == 404
    assert (
        cx.post(
            reverse("events:respond", args=[ev.public_id]), {"response": "accepted"}
        ).status_code
        == 404
    )
    cg.post(reverse("events:respond", args=[ev.public_id]), {"response": "accepted"})
    assert Guest.objects.get().response == "accepted"
    cg.post(reverse("events:respond", args=[ev.public_id]), {"response": "needs_action"})
    assert Guest.objects.get().response == "accepted"  # an invalid answer changes nothing
    # cancel needs the confirm field
    co.post(reverse("events:cancel", args=[ev.public_id]), {})
    ev.refresh_from_db()
    assert ev.status == "confirmed"
    co.post(reverse("events:cancel", args=[ev.public_id]), {"confirm": "yes"})
    ev.refresh_from_db()
    assert ev.status == "cancelled" and ev.version == 2
    assert b"Cancelled" in cg.get(url).content


@pytest.mark.django_db
def test_form_errors_keep_values_escape_and_unknown_guest_creates_nothing():
    org = _u("F-ORG")
    c = _c(org)
    r = c.post(reverse("events:new"), {**FORM, "title": "<b>x</b>", "guests": "NOBODY-1"})
    assert r.status_code == 400 and Event.objects.count() == 0
    assert b"&lt;b&gt;x&lt;/b&gt;" in r.content and b"<b>x</b>" not in r.content
    for bad in ({"end": "2026-12-01T09:00"}, {"start": "garbage"}, {"tz": "Mars/Base"}):
        assert c.post(reverse("events:new"), {**FORM, **bad}).status_code == 400
    assert Event.objects.count() == 0


@pytest.mark.django_db
def test_edit_saves_and_a_stale_form_gets_a_conflict_page_with_typed_values():
    org = _u("E-ORG")
    c = _c(org)
    c.post(reverse("events:new"), FORM)
    ev = Event.objects.get()
    u = reverse("events:edit", args=[ev.public_id])
    assert b'name="version" value="1"' in c.get(u).content
    assert c.post(u, {**FORM, "title": "Renamed", "version": 1}).status_code == 302
    r = c.post(u, {**FORM, "title": "Typed stale", "version": 1})
    assert r.status_code == 409 and b"Typed stale" in r.content and b"version 2" in r.content
    assert b'name="version" value="2"' in r.content  # resubmitting is a knowing overwrite
    ev.refresh_from_db()
    assert ev.title == "Renamed"


@pytest.mark.django_db
def test_busy_page_shows_times_only_and_bad_input_is_a_message():
    org, other = _u("B-ORG"), _u("B-OTH")
    _c(org).post(reverse("events:new"), {**FORM, "title": "Hidden title"})
    r = _c(other).get(reverse("events:busy"), {"date": "2026-12-01", "people": "B-ORG, B-OTH"})
    body = r.content.decode()
    assert "10:00 to 11:00" in body and "Free all day" in body and "Hidden title" not in body
    assert (
        _c(other).get(reverse("events:busy"), {"date": "nope", "people": "B-ORG"}).status_code
        == 200
    )
    assert (
        _c(other)
        .get(reverse("events:busy"), {"date": "2026-12-01", "people": "NOBODY"})
        .status_code
        == 200
    )


@pytest.mark.django_db
def test_hosts_anonymous_csrf_and_nav():
    org = _u("H-ORG")
    c = _c(org)
    assert c.get("/calendar/", HTTP_HOST=HOST).status_code == 200
    blocked = c.get("/calendar/", HTTP_HOST="iam.salowtech.com")
    assert blocked.status_code == 404 and b"not accessible" in blocked.content
    assert (
        b"/calendar/" in c.get("/tasks/", HTTP_HOST=HOST).content
    )  # nav link on the workspace host
    anon = Client(HTTP_HOST=HOST)
    assert anon.get("/calendar/").status_code == 302
    c.post(reverse("events:new"), FORM)
    ev = Event.objects.get()
    strict = Client(enforce_csrf_checks=True, HTTP_HOST=HOST)
    strict.force_login(org)
    posts = [
        reverse("events:new"),
        reverse("events:edit", args=[ev.public_id]),
        reverse("events:respond", args=[ev.public_id]),
        reverse("events:cancel", args=[ev.public_id]),
    ]
    for u in posts:
        assert (
            strict.post(
                u, {**FORM, "version": 1, "confirm": "yes", "response": "accepted"}
            ).status_code
            == 403
        )
        assert anon.post(u, {}).status_code == 302
    ev.refresh_from_db()
    assert ev.status == "confirmed" and ev.version == 1 and Event.objects.count() == 1


@pytest.mark.django_db
def test_event_in_another_zone_shows_that_zones_clock_time_everywhere():
    org, g = _u("Z-ORG"), _u("Z-G")
    c, cg = _c(org), _c(g)
    c.post(reverse("events:new"), {**FORM, "tz": "America/New_York", "guests": "Z-G"})
    ev = Event.objects.get()  # 10:00 to 11:00 New York on 2026-12-01
    page = c.get(reverse("events:detail", args=[ev.public_id])).content.decode()
    assert "10:00 to 11:00 (America/New_York)" in page
    edit = c.get(reverse("events:edit", args=[ev.public_id])).content.decode()
    assert 'value="2026-12-01T10:00"' in edit and 'value="2026-12-01T11:00"' in edit
    import datetime as _dt

    from apps.events import views

    class _Fixed:
        @staticmethod
        def now():
            return _dt.datetime(2026, 11, 30, tzinfo=_dt.timezone.utc)

    views_timezone = views.timezone
    views.timezone = _Fixed
    try:
        agenda = c.get(reverse("events:home")).content.decode()
    finally:
        views.timezone = views_timezone
    assert "10:00 to 11:00 (America/New_York)" in agenda
    assert "in Asia/Kolkata" not in agenda and "own time zone" in agenda
    # agenda uses a window around now, so check the same formatting through the busy page too
    # (busy always shows the site zone, Asia/Kolkata: 10:00 New York is 20:30 in Kolkata)
    r = cg.get(reverse("events:busy"), {"date": "2026-12-01", "people": "Z-ORG"}).content.decode()
    assert "20:30 to 21:30" in r


@pytest.mark.django_db
def test_bad_version_is_a_400_and_guest_errors_do_not_reveal_existence_and_throttle_shows(
    monkeypatch,
):
    org, g, off = _u("M-ORG"), _u("M-G"), _u("M-OFF")
    CustomUser.objects.filter(pk=off.pk).update(status="suspended")
    c = _c(org)
    c.post(reverse("events:new"), FORM)
    ev = Event.objects.get()
    u = reverse("events:edit", args=[ev.public_id])
    for bad in ("abc", "", "1.5", "-1", "9" * 40):
        r = c.post(
            u, {**FORM, "title": "Changed", "version": bad}
        )  # a real change, so it is checked
        want = 409 if bad in ("-1", "9" * 40) else 400
        assert r.status_code == want, (bad, r.status_code)
    assert c.post(u, {**FORM, "version": "abc"}).status_code == 400
    msgs = set()
    for guests in ("M-OFF", "NOBODY-9"):
        r = c.post(reverse("events:new"), {**FORM, "guests": guests})
        assert r.status_code == 400
        msgs.add(
            r.content.decode().split("Some guests")[1][:60]
            if "Some guests" in r.content.decode()
            else r.content.decode()
        )
    assert len(msgs) == 1  # same wording for suspended and for unknown
    from apps.events import views

    monkeypatch.setattr(views, "throttled", lambda *a, **k: True)
    cg = _c(g)
    for name, data in (("respond", {"response": "accepted"}), ("cancel", {"confirm": "yes"})):
        who = cg if name == "respond" else c
        Guest.objects.get_or_create(event=ev, person=g)
        r = who.post(reverse(f"events:{name}", args=[ev.public_id]), data, follow=True)
        assert b"Too many changes" in r.content
    ev.refresh_from_db()
    assert ev.status == "confirmed"


def _mk(org, title, start, end, guests=()):
    import datetime as dt

    ev = Event.objects.create(
        organizer=org, title=title, tz="Asia/Kolkata",
        start_utc=dt.datetime.fromisoformat(start), end_utc=dt.datetime.fromisoformat(end),
    )  # fmt: skip
    for g in guests:
        Guest.objects.create(event=ev, person=g)
    return ev


@pytest.mark.django_db
def test_month_grid_placement_zone_boundaries_privacy_and_errors():
    org, g, out = _u("M-ORG"), _u("M-G"), _u("M-OUT")
    url = reverse("events:month")
    # 2026-11-30 20:00 UTC is 01:30 IST on 1 Dec: it belongs to December, not November
    _mk(org, "Late-night", "2026-11-30T20:00:00+00:00", "2026-11-30T20:30:00+00:00", [g])
    # spans 30 Nov 10:00 IST to 2 Dec 10:00 IST: on 30 Nov, 1 Dec, 2 Dec
    _mk(org, "Offsite", "2026-11-30T04:30:00+00:00", "2026-12-02T04:30:00+00:00")
    # ends exactly at local midnight: not on the next day
    _mk(org, "Ends-midnight", "2026-12-04T17:30:00+00:00", "2026-12-04T18:30:00+00:00")
    _mk(org, "Secret", "2026-12-10T05:00:00+00:00", "2026-12-10T06:00:00+00:00")
    cancelled = _mk(org, "Gone", "2026-12-11T05:00:00+00:00", "2026-12-11T06:00:00+00:00")
    cancelled.status = "cancelled"
    cancelled.save()
    r = _c(g).get(url, {"m": "2026-12"})
    assert r.status_code == 200
    body = r.content.decode()
    assert "Late-night" in body and "Secret" not in body and "Gone" not in body
    assert "Offsite" not in body  # g is not a guest of it
    ro = _c(org).get(url, {"m": "2026-12"})
    days = {d["date"].isoformat(): d for w in ro.context["weeks"] for d in w}
    names = lambda k: [x["e"].title for x in days[k]["shown"]]  # noqa: E731
    assert "Late-night" in names("2026-12-01") and "Late-night" not in names("2026-11-30")
    assert all("Offsite" in names(k) for k in ("2026-11-30", "2026-12-01", "2026-12-02"))
    assert "Offsite" not in names("2026-12-03")
    assert "Ends-midnight" in names("2026-12-04") and "Ends-midnight" not in names("2026-12-05")
    assert "Gone" not in str(days["2026-12-11"])
    # grid is Monday first and whole weeks
    assert ro.context["weeks"][0][0]["date"].weekday() == 0
    assert all(len(w) == 7 for w in ro.context["weeks"])
    assert "Secret" not in _c(out).get(url, {"m": "2026-12"}).content.decode()
    for bad in ("2026-13", "x", "1999-12", "2026-12-01"):
        assert _c(org).get(url, {"m": bad}).status_code == 400
    assert Client(HTTP_HOST=HOST).get(url).status_code == 302
    assert _c(org).get(url).status_code == 200  # default month


@pytest.mark.django_db
def test_month_cell_cap_counts_the_rest_and_escapes_titles():
    org = _u("C-ORG")
    for i in range(5):
        _mk(org, f"<b>E{i}</b>", f"2026-12-15T0{i}:00:00+00:00", f"2026-12-15T0{i}:30:00+00:00")
    body = _c(org).get(reverse("events:month"), {"m": "2026-12"}).content.decode()
    assert "+2 more" in body and "<b>E0</b>" not in body and "&lt;b&gt;E0" in body
