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
    for name in ("toggle", "delete"):
        assert cb.post(reverse(f"tasks:{name}", args=[t.public_id])).status_code == 404
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
