"""Search no-leak cases for task titles that the existing finder tests do not exercise:
a removed project member, a deleted project, a deleted project task and a deleted task list.
These assert the searchers' current behaviour only (apps/finder/searchers.py: search_tasks)."""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.tasks import projects as pj
from apps.tasks import services as ts
from apps.tasks.models import ProjectMember, ProjectTask, Task

HOST = "app.salowtech.com"


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip


def _body(u, q):
    c = Client(HTTP_HOST=HOST)
    c.force_login(u)
    return c.get(reverse("finder:search"), {"q": q}).content.decode()


@pytest.mark.django_db
def test_project_task_titles_follow_membership_and_deletion():
    a, b = _u("HT-A"), _u("HT-B")
    p = pj.create_project(a, "Hidden task project")
    task = pj.add_task(a, p, p.columns.first().pk, "Narwhal project item")
    assert "Narwhal project item" in _body(a, "narwhal")
    assert "Narwhal project item" not in _body(b, "narwhal")  # never a member
    m = ProjectMember.objects.create(project=p, person=b, role=ProjectMember.MEMBER, added_by=a)
    assert "Narwhal project item" in _body(b, "narwhal")
    ProjectMember.objects.filter(pk=m.pk).update(removed_at=timezone.now())
    assert "Narwhal project item" not in _body(b, "narwhal")  # removed member
    assert "Narwhal project item" in _body(a, "narwhal")
    ProjectTask.objects.filter(pk=task.pk).update(deleted_at=timezone.now())
    assert "Narwhal project item" not in _body(a, "narwhal")  # deleted task
    ProjectTask.objects.filter(pk=task.pk).update(deleted_at=None)
    type(p).objects.filter(pk=p.pk).update(deleted_at=timezone.now())
    assert "Narwhal project item" not in _body(a, "narwhal")  # deleted project


@pytest.mark.django_db
def test_task_in_a_deleted_list_is_not_found_by_owner_or_assignee():
    a, b = _u("HL-A"), _u("HL-B")
    tl = ts.create_list(a, "Soon gone")
    t = ts.create_task(a, tl, "Walrus list item")
    ts.assign(a, t, b)
    assert "Walrus list item" in _body(a, "walrus") and "Walrus list item" in _body(b, "walrus")
    type(tl).objects.filter(pk=tl.pk).update(deleted_at=timezone.now())
    assert "Walrus list item" not in _body(a, "walrus")
    assert "Walrus list item" not in _body(b, "walrus")
    assert Task.objects.filter(pk=t.pk).exists()  # hidden from search, not removed
