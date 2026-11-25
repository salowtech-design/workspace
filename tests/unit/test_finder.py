import datetime as dt

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.events import services as es
from apps.tasks import projects as pj
from apps.tasks import services as ts

HOST = "app.salowtech.com"


def _u(eid, role="employee", status="active"):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role=role, status=status, is_active=True,
    )  # fmt: skip


def _c(u):
    c = Client(HTTP_HOST=HOST)
    c.force_login(u)
    return c


def _area(html):
    """The results area: everything after the fixed help line under the search form."""
    marker = "are not part of search yet."
    assert marker in html
    return html.split(marker, 1)[1]


def _seed():
    a, b = _u("SR-A"), _u("SR-B")
    tl = ts.create_list(a, "Mine")
    ts.create_task(a, tl, "Zebra budget plan")
    ts.create_task(b, ts.create_list(b, "Theirs"), "Zebra secret of b")
    p = pj.create_project(a, "Proj")
    pj.add_task(a, p, p.columns.first().pk, "Zebra project item")
    D = dt.datetime
    es.create_event(a, "Zebra review", D(2026, 12, 1, 10), D(2026, 12, 1, 11), "Asia/Kolkata")
    es.create_event(b, "Zebra private b", D(2026, 12, 1, 12), D(2026, 12, 1, 13), "Asia/Kolkata")
    return a, b, p


@pytest.mark.django_db
def test_search_shows_only_own_items_grouped_and_never_leaks_other_titles():
    a, b, p = _seed()
    body = _c(a).get(reverse("finder:search"), {"q": "zebra"}).content.decode()  # lower case
    for ok in ("Zebra budget plan", "Zebra project item", "Zebra review"):
        assert ok in body
    for hidden in ("secret of b", "private b"):
        assert hidden not in body
    assert (
        "<h3>Tasks</h3>" in body
        and "<h3>Calendar</h3>" in body
        and "<h3>Project tasks</h3>" in body
    )
    # people: finds by name or ID, shows nothing sensitive
    from apps.directory.models import EmployeeProfile

    EmployeeProfile.objects.update_or_create(
        user=b, defaults={"phone": "555-9999", "emergency_contact": "EmergencyX"}
    )
    r = _c(a).get(reverse("finder:search"), {"q": "sr-b", "in": "people"}).content.decode()
    assert "Person SR-B (SR-B)" in r and "555-9999" not in r and "EmergencyX" not in r
    assert "sr-b@salowtech.com" not in r


@pytest.mark.django_db
def test_membership_assignment_decline_and_cancel_change_what_is_found():
    a, b, p = _seed()
    cb = _c(b)
    assert (
        "Zebra project item"
        not in cb.get(reverse("finder:search"), {"q": "zebra"}).content.decode()
    )
    D = dt.datetime
    ev = es.create_event(
        a, "Zebra shared", D(2026, 12, 2, 10), D(2026, 12, 2, 11), "Asia/Kolkata", [b.pk]
    )
    assert "Zebra shared" in cb.get(reverse("finder:search"), {"q": "zebra"}).content.decode()
    es.cancel_event(a, ev.public_id)
    assert "Zebra shared" not in cb.get(reverse("finder:search"), {"q": "zebra"}).content.decode()
    tl = ts.create_list(a, "L2")
    t = ts.create_task(a, tl, "Zebra given to b")
    ts.assign(a, t, b)
    assert "Zebra given to b" in cb.get(reverse("finder:search"), {"q": "zebra"}).content.decode()
    ts.delete_task(a, t)
    assert (
        "Zebra given to b" not in cb.get(reverse("finder:search"), {"q": "zebra"}).content.decode()
    )


@pytest.mark.django_db
def test_query_rules_filters_throttle_hosts_anonymous_and_no_result_wording(monkeypatch):
    a, b, p = _seed()
    c = _c(a)
    assert c.get(reverse("finder:search"), {"q": "z"}).status_code == 400
    assert c.get(reverse("finder:search"), {"q": "x" * 101}).status_code == 400
    assert c.get(reverse("finder:search")).status_code == 200
    only = c.get(reverse("finder:search"), {"q": "zebra", "in": "calendar"}).content.decode()
    assert "Zebra review" in only and "Zebra budget plan" not in only
    miss = c.get(reverse("finder:search"), {"q": "zzzqqq"}).content.decode()
    hidden = c.get(reverse("finder:search"), {"q": "secret of b"}).content.decode()

    # a hidden item and a missing one give the same results area (only the typed query differs)
    assert "No results" in _area(miss) and _area(miss) == _area(hidden)
    assert Client(HTTP_HOST=HOST).get("/search/?q=zebra").status_code == 302
    blocked = c.get("/search/?q=zebra", HTTP_HOST="iam.salowtech.com")
    assert blocked.status_code == 404 and b"not accessible" in blocked.content
    from apps.finder import views

    monkeypatch.setattr(views, "throttled", lambda *a, **k: True)
    r = c.get(reverse("finder:search"), {"q": "zebra"})
    assert r.status_code == 429 and b"Too many searches" in r.content


@pytest.mark.django_db
def test_inactive_or_suspended_people_find_nothing_and_are_not_found():
    a, b, p = _seed()
    _u("SR-OFF", status="suspended")
    r = _c(a).get(reverse("finder:search"), {"q": "sr-off", "in": "people"}).content.decode()
    assert "SR-OFF" not in _area(r) and "No results" in _area(r)
    CustomUser.objects.filter(pk=a.pk).update(status="suspended")
    c = _c(CustomUser.objects.get(pk=a.pk))
    r = c.get(reverse("finder:search"), {"q": "zebra"})
    assert r.status_code == 302 and "Zebra budget plan" not in r.content.decode()
