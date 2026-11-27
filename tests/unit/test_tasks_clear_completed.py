import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog
from apps.tasks import services as svc
from apps.tasks.models import Task
from tests.unit.test_tasks_services import _u


@pytest.fixture
def setup(db):
    a, b = _u("CC-A"), _u("CC-B")
    return a, b, svc.create_list(a, "L")


def _c(u):
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(u)
    return c


def _live(tl):
    return sorted(Task.objects.filter(task_list=tl, deleted_at__isnull=True).values_list("title", flat=True))


def _done(a, t):
    svc.set_status(a, Task.objects.get(pk=t.pk), True)


def test_clear_moves_only_completed_tasks_and_each_restores_alone(setup):
    a, b, tl = setup
    d1, d2, keep = (svc.create_task(a, tl, n) for n in ("D1", "D2", "Keep"))
    kid = svc.create_task(a, tl, "Kid", parent=d1)
    openparent = svc.create_task(a, tl, "OpenP")
    donekid = svc.create_task(a, tl, "DoneKid", parent=openparent)
    for t in (d1, d2, donekid):
        _done(a, t)
    assert svc.clear_completed(a, tl) == 4  # D1 with its subtask Kid, D2, DoneKid
    assert _live(tl) == ["Keep", "OpenP"]
    assert AuditLog.objects.filter(action="tasks.list.clear_completed").count() == 1
    _, trashed = svc.trash_for(a)
    assert sorted(t.title for t in trashed) == ["D1", "D2", "DoneKid"]  # Kid is inside D1's deletion
    batches = {Task.objects.get(pk=t.pk).delete_batch for t in (d1, d2, donekid)}
    assert len(batches) == 3 and Task.objects.get(pk=kid.pk).delete_batch == Task.objects.get(pk=d1.pk).delete_batch
    svc.restore_task(a, Task.objects.get(pk=d1.pk))  # established restore: D1 with Kid, and nothing else
    assert _live(tl) == ["D1", "Keep", "Kid", "OpenP"]
    assert Task.objects.get(pk=d2.pk).deleted_at is not None


def test_nothing_completed_is_refused_and_changes_nothing(setup):
    a, b, tl = setup
    svc.create_task(a, tl, "Open")
    n = AuditLog.objects.count()
    with pytest.raises(ValidationError, match="no completed tasks"):
        svc.clear_completed(a, tl)
    assert _live(tl) == ["Open"] and AuditLog.objects.count() == n


def test_stranger_is_refused_and_an_assigned_completed_task_is_cleared_by_the_owner(setup):
    a, b, tl = setup
    t = svc.create_task(a, tl, "Given")
    svc.assign(a, Task.objects.get(pk=t.pk), b)
    svc.set_status(b, Task.objects.get(pk=t.pk), True)  # the assignee completes it
    with pytest.raises(PermissionDenied):
        svc.clear_completed(b, tl)
    assert _live(tl) == ["Given"]
    assert svc.clear_completed(a, tl) == 1 and _live(tl) == []


def test_other_lists_are_untouched(setup):
    a, b, tl = setup
    other = svc.create_list(a, "M")
    x, y = svc.create_task(a, tl, "X"), svc.create_task(a, other, "Y")
    _done(a, x)
    _done(a, y)
    svc.clear_completed(a, tl)
    assert _live(other) == ["Y"]


def test_views_filter_and_clear(setup):
    a, b, tl = setup
    d, o = svc.create_task(a, tl, "Finished"), svc.create_task(a, tl, "Pending")
    k = svc.create_task(a, tl, "DoneSub", parent=o)
    _done(a, d)
    _done(a, k)
    url = reverse("tasks:list", args=[tl.public_id])
    full = _c(a).get(url).content.decode()
    assert "Finished" in full and "DoneSub" in full and "Clear completed (2)" in full and "Hide completed" in full
    hid = _c(a).get(url + "?show=open").content.decode()
    assert "Pending" in hid and "Finished" not in hid and "DoneSub" not in hid and "Show completed" in hid and "Move Pending" not in hid
    assert _live(tl) == ["DoneSub", "Finished", "Pending"]  # the filter deleted nothing
    clr = reverse("tasks:clear_completed", args=[tl.public_id])
    assert _c(b).post(clr).status_code == 404 and _c(a).get(clr).status_code == 405
    r = _c(a).post(clr, follow=True)
    assert b"Moved 2 completed tasks to the Trash." in r.content and _live(tl) == ["Pending"]
    r = _c(a).post(clr, follow=True)
    assert b"no completed tasks to clear" in r.content
    assert b"Clear completed" not in _c(a).get(url).content
