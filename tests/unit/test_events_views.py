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
