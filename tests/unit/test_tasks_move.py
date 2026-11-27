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
    a, b = _u("M-A"), _u("M-B")
    return a, b, svc.create_list(a, "One"), svc.create_list(a, "Two")


def _c(u):
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(u)
    return c


def _v(t):
    return Task.objects.get(pk=t.pk).version


def test_move_goes_to_the_end_of_the_target_with_subtasks_and_a_version_bump(setup):
    a, b, one, two = setup
    svc.create_task(a, two, "Already there")
    p = svc.create_task(a, one, "Parent")
    kid = svc.create_task(a, one, "Kid", parent=p)
    gone = svc.create_task(a, one, "Deleted kid", parent=p)
    svc.delete_task(a, gone)
    v = _v(p)
    out = svc.move_task(a, p, v, two)
    assert out.task_list_id == two.pk and out.version == v + 1
    assert [t.title for t in svc.tasks_in(a, two)] == ["Already there", "Parent", "Kid"]
    assert svc.tasks_in(a, one).count() == 0
    assert Task.objects.get(pk=gone.pk).task_list_id == two.pk  # the deleted kid moved too
    svc.restore_task(a, Task.objects.get(pk=gone.pk))  # still restorable under its parent
    assert {t.title for t in svc.tasks_in(a, two)} == {"Already there", "Parent", "Kid", "Deleted kid"}
    assert Task.objects.get(pk=kid.pk).version == 2
    assert AuditLog.objects.filter(action="tasks.task.move").count() == 1


def test_an_assigned_task_keeps_its_assignee_and_stays_visible_to_them(setup):
    a, b, one, two = setup
    t = svc.create_task(a, one, "Given")
    svc.assign(a, t, b)
    svc.move_task(a, Task.objects.get(pk=t.pk), _v(t), two)
    assert [x.title for x in svc.assigned_to(b)] == ["Given"]
    assert Task.objects.get(pk=t.pk).assignee_id == b.pk


def test_same_list_is_a_no_op_and_a_subtask_alone_is_refused(setup):
    a, b, one, two = setup
    p = svc.create_task(a, one, "P")
    kid = svc.create_task(a, one, "K", parent=p)
    v = _v(p)
    assert svc.move_task(a, p, v, one).version == v
    assert AuditLog.objects.filter(action="tasks.task.move").count() == 0
    with pytest.raises(ValidationError, match="stays in its parent task's list"):
        svc.move_task(a, kid, _v(kid), two)
    assert Task.objects.get(pk=kid.pk).task_list_id == one.pk


def test_stale_version_stranger_foreign_target_and_deleted_cases_are_refused(setup):
    a, b, one, two = setup
    t = svc.create_task(a, one, "T")
    seen = _v(t)
    svc.update_task(a, Task.objects.get(pk=t.pk), seen, title="T2")
    with pytest.raises(svc.StaleVersion):
        svc.move_task(a, Task.objects.get(pk=t.pk), seen, two)  # the form was opened before the edit
    theirs = svc.create_list(b, "Theirs")
    with pytest.raises(PermissionDenied):
        svc.move_task(a, Task.objects.get(pk=t.pk), _v(t), theirs)  # not the owner of the target
    with pytest.raises(PermissionDenied):
        svc.move_task(b, Task.objects.get(pk=t.pk), _v(t), theirs)  # not the owner of the task
    svc.delete_list(a, two)
    with pytest.raises(PermissionDenied):
        svc.move_task(a, Task.objects.get(pk=t.pk), _v(t), two)  # a deleted target
    svc.delete_task(a, Task.objects.get(pk=t.pk))
    with pytest.raises(PermissionDenied):
        svc.move_task(a, Task.objects.get(pk=t.pk), _v(t), one)  # a deleted task
    assert Task.objects.get(pk=t.pk).task_list_id == one.pk


@pytest.mark.django_db(transaction=True)
def test_opposite_moves_forced_to_overlap_lock_in_one_order(monkeypatch):
    """X goes one to two while Y goes two to one; each waits at the pause (after its checks, before its write) for the other. Local TiDB 8.5.1
    unistore evidence, not production-cluster concurrency. Both must finish, none deadlock, and both end in the right list."""
    import threading

    from django.db import connection

    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    a = _u("M-OPP")
    one, two = svc.create_list(a, "One"), svc.create_list(a, "Two")
    x, y = svc.create_task(a, one, "X"), svc.create_task(a, two, "Y")
    barrier, arrived = threading.Barrier(2), []

    def pause():
        arrived.append(1)
        try:
            barrier.wait(timeout=2)
        except threading.BrokenBarrierError:
            pass  # the second move was kept out until the first committed, which is the ordered lock working

    monkeypatch.setattr(svc, "_pause", pause)
    out = _threads([lambda: svc.move_task(a, x, _v(x), two), lambda: svc.move_task(a, y, _v(y), one)])
    assert out == ["ok", "ok"], out
    assert Task.objects.get(pk=x.pk).task_list_id == two.pk and Task.objects.get(pk=y.pk).task_list_id == one.pk
    assert len(arrived) == 2


def test_view_moves_with_the_version_and_404s_for_strangers_and_foreign_targets(setup):
    a, b, one, two = setup
    t = svc.create_task(a, one, "T")
    url = reverse("tasks:move", args=[t.public_id])
    assert _c(b).post(url, {"to": str(two.public_id), "version": _v(t)}).status_code == 404
    theirs = svc.create_list(b, "Theirs")
    assert _c(a).post(url, {"to": str(theirs.public_id), "version": _v(t)}).status_code == 404
    assert _c(a).get(url).status_code == 405
    r = _c(a).post(url, {"to": str(two.public_id), "version": _v(t) + 5}, follow=True)
    assert b"changed while you were working" in r.content and Task.objects.get(pk=t.pk).task_list_id == one.pk
    r = _c(a).post(url, {"to": str(two.public_id), "version": _v(t)}, follow=True)
    assert b"Task moved." in r.content and Task.objects.get(pk=t.pk).task_list_id == two.pk
