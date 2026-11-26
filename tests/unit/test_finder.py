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
    assert "<h3>Tasks</h3>" in body and "<h3>Calendar</h3>" in body
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


@pytest.mark.django_db
def test_everywhere_gives_each_module_its_own_share_and_says_when_it_truncates():
    a = _u("QA-VIEW")
    for i in range(25):
        CustomUser.objects.create(
            employee_id=f"QA-{i:03d}", full_name=f"Quokka Person {i}", role="employee",
            status="active", is_active=True,
        )  # fmt: skip
    ts.create_task(a, ts.create_list(a, "L"), "Quokka task")
    body = _c(a).get(reverse("finder:search"), {"q": "quokka"}).content.decode()
    assert "Quokka task" in body and "<h3>Tasks</h3>" in body  # not starved by 25 people
    assert body.count("Quokka Person") == 20
    assert "Showing the first 20 matches here" in body
    assert body.count("Showing the first 20") == 1  # only the module that was cut
    people = _c(a).get(reverse("finder:search"), {"q": "quokka", "in": "people"})
    assert people.content.decode().count("Quokka Person") == 20


@pytest.mark.django_db
def test_unknown_module_filter_is_a_400_and_copy_names_the_fields_searched():
    a = _u("QB-VIEW")
    r = _c(a).get(reverse("finder:search"), {"q": "zebra", "in": "bogus"})
    assert r.status_code == 400 and b"Unknown place to search" in r.content
    ok = _c(a).get(reverse("finder:search")).content.decode()
    assert "file and folder names in Drive" in ok and "does not search file contents" in ok


def test_person_link_is_rebuilt_from_a_checked_id():
    from apps.core.foundation.search import SearchHit
    from apps.finder.views import _link

    good = SearchHit("person", "1", "x", "/directory/profile/ST-1/")
    assert _link(good) == "/directory/profile/ST-1/"
    bad = SearchHit("person", "1", "x", "/directory/profile/a?b=1#c/")
    assert _link(bad) == "/directory/"


@pytest.mark.django_db
def test_project_names_are_found_by_members_only_and_not_after_removal_or_delete():
    from django.utils import timezone

    a, b = _u("PN-A"), _u("PN-B")
    p = pj.create_project(a, "Quokka launch")
    hidden = pj.create_project(b, "Quokka private of b")

    def body(u, q="quokka"):
        return _c(u).get(reverse("finder:search"), {"q": q}).content.decode()

    html = body(a)  # lower case query, member (lead)
    assert "Quokka launch" in html and f"/tasks/projects/{p.public_id}/" in html
    assert "private of b" not in html
    assert "Quokka launch" not in body(b)  # a non-member gets nothing
    from apps.tasks.models import ProjectMember

    m = ProjectMember.objects.create(project=p, person=b, role=ProjectMember.MEMBER, added_by=a)
    assert "Quokka launch" in body(b)  # a member finds it
    ProjectMember.objects.filter(pk=m.pk).update(removed_at=timezone.now())
    assert "Quokka launch" not in body(b)  # a removed member does not
    type(p).objects.filter(pk=p.pk).update(deleted_at=timezone.now())
    assert "Quokka launch" not in body(a)
    assert "Quokka private of b" in body(b) and hidden.pk
    assert "project names" in body(a, "zz")


@pytest.mark.django_db
def test_post_titles_are_found_only_by_people_the_posts_module_lets_see_them():
    from apps.directory.models import Department, Team, TeamMembership
    from apps.posts import services as ps

    dept = Department.objects.create(code="sp", name="Search Posts")
    mgr = _u("SP-MGR", role="manager")
    team = Team.objects.create(code="sp-t", name="Search Team", department=dept, lead=mgr)
    a, b, other = _u("SP-A"), _u("SP-B"), _u("SP-OTHER")
    TeamMembership.objects.create(team=team, user=a)
    TeamMembership.objects.create(team=team, user=b)
    ps.create_post(author=a, title="Quokka standup notes", content="plain body one")
    ps.create_post(author=b, title="Quokka other member", content="plain body two")
    ps.create_post(author=other, title="Quokka outside team", content="plain body three")
    ps.create_post(author=a, title="Plain title", content="the word quokka only in the body")
    url = reverse("finder:search")

    def found(u, **kw):
        body = _c(u).get(url, {"q": "quokka", **kw}).content.decode()
        return {t for t in ("Quokka standup notes", "Quokka other member", "Quokka outside team", "Plain title") if t in body}

    # the posts module's rule: an employee sees own and own-team posts (not other teams); a content-only match is not a hit
    assert found(a) == {"Quokka standup notes", "Quokka other member"}
    assert found(other) == {"Quokka outside team"}
    # the team lead finds the team's posts, and still not posts with no team
    assert found(mgr) == {"Quokka standup notes", "Quokka other member"}
    body = _c(a).get(url, {"q": "quokka", "in": "posts"}).content.decode()
    assert "<h3>Work Logs &amp; Feed</h3>" in body and 'href="/posts/' in body
    # a hit opens the post page for the person who found it
    assert _c(a).get(_hit_url(body)).status_code == 200


def _hit_url(body):
    import re

    return re.search(r'href="(/posts/\d+/)"', body).group(1)


@pytest.mark.django_db
def test_post_search_hits_match_post_page_access_for_every_recognized_role():
    from apps.directory.models import Department, Team, TeamMembership
    from apps.posts import services as ps

    dept = Department.objects.create(code="pp", name="Parity Dept")
    lead = _u("PP-LEAD", role="manager")
    team = Team.objects.create(code="pp-t", name="Parity Team", department=dept, lead=lead)
    author = _u("PP-AUTH")
    TeamMembership.objects.create(team=team, user=author)
    outsider = _u("PP-OUT")
    posts = {
        "Kiwi in team": ps.create_post(author=author, title="Kiwi in team", content="b1"),
        "Kiwi no team": ps.create_post(author=outsider, title="Kiwi no team", content="b2"),
    }
    viewers = {r: _u("PP-R%d" % i, role=r) for i, r in enumerate(CustomUser.Role.values)}
    viewers["lead"], viewers["author"], viewers["outsider"] = lead, author, outsider
    for name, u in viewers.items():
        c = _c(u)
        body = c.get(reverse("finder:search"), {"q": "kiwi", "in": "posts"}).content.decode()
        for title, p in posts.items():
            allowed = c.get(reverse("posts:detail", args=[p.pk])).status_code == 200
            assert (title in body) == allowed, (name, title, allowed)
