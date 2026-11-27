import datetime

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.tasks import services as svc
from apps.tasks.models import Task
from tests.unit.test_tasks_services import _u


def _c(u):
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(u)
    return c


def test_list_and_assigned_pages_mark_only_open_tasks_due_before_today_as_overdue(db):
    a, b = _u("O-A"), _u("O-B")
    tl = svc.create_list(a, "L")
    today = timezone.localdate()
    day = datetime.timedelta(days=1)
    svc.create_task(a, tl, "Late open", due_date=today - day)
    svc.create_task(a, tl, "Due today", due_date=today)
    svc.create_task(a, tl, "Later", due_date=today + day)
    done = svc.create_task(a, tl, "Late done", due_date=today - day)
    svc.set_status(a, Task.objects.get(pk=done.pk), True)
    given = svc.create_task(a, tl, "Late given", due_date=today - day)
    svc.assign(a, Task.objects.get(pk=given.pk), b)
    html = _c(a).get(reverse("tasks:list", args=[tl.public_id])).content.decode()
    assert html.count("(overdue)") == 2  # Late open and Late given; not today, not later, not the completed one
    assert html.count("task-overdue") == 2
    assert "Late done" in html
    page = _c(b).get(reverse("tasks:assigned")).content.decode()
    assert page.count("(overdue)") == 1 and "Late given" in page


def test_a_subtask_can_be_edited_from_the_list_page_and_shows_its_notes_and_due_date(db):
    a = _u("S-A")
    tl = svc.create_list(a, "L")
    p = svc.create_task(a, tl, "Parent")
    kid = svc.create_task(a, tl, "Kid", notes="kid notes", due_date=timezone.localdate() - datetime.timedelta(days=2), parent=p)
    page = _c(a).get(reverse("tasks:list", args=[tl.public_id])).content.decode()
    edit = reverse("tasks:edit", args=[kid.public_id])
    assert edit in page and "kid notes" in page and page.count("(overdue)") == 1  # the kid's own overdue date
    r = _c(a).post(edit, {"version": Task.objects.get(pk=kid.pk).version, "title": "Kid renamed", "notes": "", "due": ""})
    assert r.status_code == 302 and r["Location"].endswith(reverse("tasks:list", args=[tl.public_id]))
    row = Task.objects.get(pk=kid.pk)
    assert (row.title, row.notes, row.due_date, row.parent_id) == ("Kid renamed", "", None, p.pk)
