import datetime as dt

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.tasks import projects
from apps.tasks import services as svc
from apps.tasks.models import Column, ProjectMember, ProjectTask, Task, TaskList


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip


def _t(tl, title, due, by, **kw):
    return Task.objects.create(task_list=tl, title=title, due_date=due, created_by=by, **kw)


def _titles(rows):
    return [r["title"] for r in rows]


@pytest.mark.django_db
def test_groups_boundaries_and_exclusions():
    a, b = _u("DS-A"), _u("DS-B")
    today = timezone.localdate()
    d = lambda n: today + dt.timedelta(days=n)  # noqa: E731
    mine = TaskList.objects.create(owner=a, name="Mine")
    theirs = TaskList.objects.create(owner=b, name="Theirs")
    _t(mine, "yesterday", d(-1), a)
    _t(mine, "todayT", d(0), a)
    _t(mine, "day7", d(7), a)
    _t(mine, "day8", d(8), a)
    _t(mine, "nodate", None, a)
    _t(mine, "finished", d(-2), a, status=Task.DONE)
    _t(mine, "removed", d(-2), a, deleted_at=timezone.now())
    _t(theirs, "others", d(0), b)  # nobody gave it to A
    _t(theirs, "given to A", d(1), b, assignee=a)
    gone = TaskList.objects.create(owner=a, name="Gone", deleted_at=timezone.now())
    _t(gone, "in deleted list", d(0), a)
    out = svc.due_soon(a, today)
    assert _titles(out["overdue"]) == ["yesterday"]
    assert _titles(out["today"]) == ["todayT"]
    assert _titles(out["soon"]) == ["given to A", "day7"]  # sorted by date; day 8 is out
    # a task in A's own list that A also is assigned to appears once
    _t(mine, "own and assigned", d(2), a, assignee=a)
    assert _titles(svc.due_soon(a, today)["soon"]).count("own and assigned") == 1


@pytest.mark.django_db
def test_stale_assignment_does_not_show_and_projects_follow_membership():
    a, b = _u("DS-C"), _u("DS-D")
    today = timezone.localdate()
    tl = TaskList.objects.create(owner=b, name="B list")
    t = _t(tl, "was given", today, b, assignee=a)
    assert _titles(svc.due_soon(a, today)["today"]) == ["was given"]
    Task.objects.filter(pk=t.pk).update(assignee=b)  # reassigned away from A
    assert svc.due_soon(a, today)["today"] == []
    p = projects.create_project(b, "Proj")
    col = Column.objects.filter(project=p).first()
    done = Column.objects.filter(project=p, name="Done").first()
    ProjectMember.objects.create(project=p, person=a, added_by=b)
    ProjectTask.objects.create(project=p, column=col, title="proj today", due_date=today, assignee=a, created_by=b)
    ProjectTask.objects.create(project=p, column=done, title="proj done", due_date=today, assignee=a, created_by=b)
    out = svc.due_soon(a, today)
    assert _titles(out["today"]) == ["proj today"]
    ProjectMember.objects.filter(project=p, person=a).update(removed_at=timezone.now())  # access lost
    assert svc.due_soon(a, today)["today"] == []
    Project = p.__class__
    ProjectMember.objects.filter(project=p, person=a).update(removed_at=None)
    Project.objects.filter(pk=p.pk).update(deleted_at=timezone.now())
    assert svc.due_soon(a, today)["today"] == []


@pytest.mark.django_db
def test_page_renders_and_requires_login_and_hides_other_people():
    a, b = _u("DS-E"), _u("DS-F")
    today = timezone.localdate()
    _t(TaskList.objects.create(owner=a, name="L"), "shows up", today, a)
    _t(TaskList.objects.create(owner=b, name="L2"), "not mine", today, b)
    anon = Client(HTTP_HOST="app.salowtech.com").get("/tasks/due/")
    assert anon.status_code == 302
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(a)
    html = c.get("/tasks/due/").content.decode()
    assert "shows up" in html and "not mine" not in html and "Due soon" in html


@pytest.mark.django_db
def test_a_task_given_to_me_links_to_assigned_not_to_the_other_persons_list():
    a, b = _u("DS-G"), _u("DS-H")
    today = timezone.localdate()
    tl = TaskList.objects.create(owner=b, name="B private list")
    _t(tl, "given", today, b, assignee=a)
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(a)
    html = c.get("/tasks/due/").content.decode()
    assert f"/tasks/list/{tl.public_id}/" not in html  # that page is 404 for a non-owner
    assert "/tasks/assigned/" in html
    assert "B private list" not in html  # the list name is not shown on the existing Assigned page either
    assert "From Person DS-H" in html  # the same owner name the Assigned page shows
    assigned = c.get("/tasks/assigned/").content.decode()
    assert "B private list" not in assigned and "From Person DS-H" in assigned
    assert c.get(f"/tasks/list/{tl.public_id}/").status_code == 404
