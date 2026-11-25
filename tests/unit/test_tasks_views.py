"""Tasks pages through the real Django views."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.tasks import services as svc
from apps.tasks.models import Task


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip


def _c(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_owner_flow_and_privacy():
    a, b = _u("W-A"), _u("W-B")
    ca, cb = _c(a), _c(b)
    r = ca.post(reverse("tasks:new_list"), {"name": "Plans"})
    tl = svc.lists_for(a).get()
    assert r.status_code == 302 and tl.name == "Plans"
    ca.post(
        reverse("tasks:add", args=[tl.public_id]), {"title": "Secret thing", "due": "2026-12-31"}
    )
    t = Task.objects.get()
    page = ca.get(reverse("tasks:list", args=[tl.public_id]))
    assert b"Secret thing" in page.content and b"31 Dec 2026" in page.content
    assert cb.get(reverse("tasks:list", args=[tl.public_id])).status_code == 404
    assert b"Secret thing" not in cb.get(reverse("tasks:assigned")).content
    assert cb.post(reverse("tasks:toggle", args=[t.public_id])).status_code == 404
    assert cb.get(reverse("tasks:delete", args=[t.public_id])).status_code == 404
    assert (
        cb.post(reverse("tasks:delete", args=[t.public_id]), {"confirm": "yes"}).status_code == 404
    )
    assert cb.post(reverse("tasks:add", args=[tl.public_id]), {"title": "x"}).status_code == 404


@pytest.mark.django_db
def test_assign_by_employee_id_and_assignee_limits():
    a, b = _u("X-A"), _u("X-B")
    ca, cb = _c(a), _c(b)
    ca.post(reverse("tasks:new_list"), {"name": "L"})
    tl = svc.lists_for(a).get()
    ca.post(reverse("tasks:add", args=[tl.public_id]), {"title": "Share me"})
    t = Task.objects.get()
    ca.post(reverse("tasks:assign", args=[t.public_id]), {"employee_id": "x-b"})  # case-insensitive
    t.refresh_from_db()
    assert t.assignee_id == b.pk
    assert b"Share me" in cb.get(reverse("tasks:assigned")).content
    cb.post(reverse("tasks:toggle", args=[t.public_id]))
    t.refresh_from_db()
    assert t.status == "completed"
    assert (
        cb.post(reverse("tasks:delete", args=[t.public_id])).status_code == 404
    )  # no delete right
    assert cb.get(reverse("tasks:list", args=[tl.public_id])).status_code == 404
    ca.post(reverse("tasks:assign", args=[t.public_id]), {"employee_id": ""})
    assert b"Share me" not in cb.get(reverse("tasks:assigned")).content


@pytest.mark.django_db
def test_bad_input_shows_a_message_and_titles_are_escaped():
    a = _u("Y-A")
    ca = _c(a)
    ca.post(reverse("tasks:new_list"), {"name": "L"})
    tl = svc.lists_for(a).get()
    ca.post(reverse("tasks:add", args=[tl.public_id]), {"title": "<script>alert(1)</script>"})
    ca.post(reverse("tasks:add", args=[tl.public_id]), {"title": "ok", "due": "not-a-date"})
    page = ca.get(reverse("tasks:list", args=[tl.public_id])).content
    assert b"<script>alert(1)" not in page and b"&lt;script&gt;" in page
    assert Task.objects.count() == 1


@pytest.mark.django_db
def test_delete_needs_confirmation_and_says_what_happens():
    a = _u("D-A")
    ca = _c(a)
    ca.post(reverse("tasks:new_list"), {"name": "L"})
    tl = svc.lists_for(a).get()
    ca.post(reverse("tasks:add", args=[tl.public_id]), {"title": "Doomed"})
    t = Task.objects.get()
    page = ca.get(reverse("tasks:delete", args=[t.public_id]))
    assert (
        page.status_code == 200
        and b"not built yet" in page.content
        and Task.objects.filter(deleted_at=None).count() == 1
    )
    ca.post(reverse("tasks:delete", args=[t.public_id]))  # no confirm field: nothing happens
    assert Task.objects.filter(deleted_at=None).count() == 1
    ca.post(reverse("tasks:delete", args=[t.public_id]), {"confirm": "yes"})
    assert Task.objects.filter(deleted_at=None).count() == 0
    assert ca.get(reverse("tasks:delete_list", args=[tl.public_id])).status_code == 200


@pytest.mark.django_db
def test_neutral_assign_errors_and_malformed_parent_and_preserved_fields():
    a, off = _u("N-A"), _u("N-OFF")
    CustomUser.objects.filter(pk=off.pk).update(status="suspended")
    ca = _c(a)
    ca.post(reverse("tasks:new_list"), {"name": "L"})
    tl = svc.lists_for(a).get()
    ca.post(reverse("tasks:add", args=[tl.public_id]), {"title": "T"})
    t = Task.objects.get()
    texts = []
    for eid in ("NOBODY-99", "N-OFF"):
        r = ca.post(reverse("tasks:assign", args=[t.public_id]), {"employee_id": eid}, follow=True)
        texts.append([str(m) for m in r.context["messages"]][-1])
    assert (
        texts[0] == texts[1] and "cannot be given" in texts[0]
    )  # unknown and inactive read the same
    assert (
        ca.post(
            reverse("tasks:add", args=[tl.public_id]), {"title": "x", "parent": "not-a-uuid"}
        ).status_code
        == 404
    )
    svc.update_task(a, t, 1, notes="keep me")
    t.refresh_from_db()
    ca.post(reverse("tasks:edit", args=[t.public_id]), {"version": t.version, "title": "Renamed"})
    t.refresh_from_db()
    assert t.title == "Renamed" and t.notes == "keep me"  # an omitted field is left alone


@pytest.mark.django_db
def test_assign_is_throttled(monkeypatch):
    from apps.tasks import views

    a = _u("H-A")
    _u("H-B")
    ca = _c(a)
    ca.post(reverse("tasks:new_list"), {"name": "L"})
    tl = svc.lists_for(a).get()
    ca.post(reverse("tasks:add", args=[tl.public_id]), {"title": "T"})
    t = Task.objects.get()
    import apps.drive.views as dv

    monkeypatch.setattr(dv, "SHARE_LIMIT", 2)
    for _ in range(3):
        ca.post(reverse("tasks:assign", args=[t.public_id]), {"employee_id": "H-B"})
    r = ca.post(reverse("tasks:assign", args=[t.public_id]), {"employee_id": "H-B"}, follow=True)
    assert any("Too many" in str(m) for m in r.context["messages"]) and views.ASSIGN_LIMIT


@pytest.mark.django_db
def test_csrf_enforced_and_logged_out_posts_refused():
    a = _u("F-A")
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(a)
    assert strict.post(reverse("tasks:new_list"), {"name": "L"}).status_code == 403
    anon = Client()
    r = anon.post(reverse("tasks:new_list"), {"name": "L"})
    assert (
        r.status_code == 302 and "login" in r["Location"].lower() or "sign" in r["Location"].lower()
    )
    assert anon.get(reverse("tasks:home")).status_code == 302
    from apps.tasks.models import TaskList

    assert TaskList.objects.count() == 0


@pytest.mark.django_db
def test_workspace_host_serves_tasks_and_iam_host_does_not():
    a = _u("P-A")
    c = _c(a)
    ok = c.get("/tasks/", HTTP_HOST="app.salowtech.com")
    assert ok.status_code == 200
    blocked = c.get("/tasks/", HTTP_HOST="iam.salowtech.com")
    assert blocked.status_code == 404 and b"not accessible" in blocked.content
    assert (
        c.get("/attendance/", HTTP_HOST="app.salowtech.com").status_code != 500
    )  # nav link resolves there too
