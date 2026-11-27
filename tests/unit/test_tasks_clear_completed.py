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


def _clear(a, tl, token=None):
    return svc.clear_completed(a, tl, svc.plan_token(svc.clear_plan(tl)) if token is None else token)


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
    assert _clear(a, tl) == (3, 1)  # heads D1, D2, DoneKid; Kid (still open) is included under D1
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
        _clear(a, tl)
    assert _live(tl) == ["Open"] and AuditLog.objects.count() == n


def test_stranger_is_refused_and_an_assigned_completed_task_is_cleared_by_the_owner(setup):
    a, b, tl = setup
    t = svc.create_task(a, tl, "Given")
    svc.assign(a, Task.objects.get(pk=t.pk), b)
    svc.set_status(b, Task.objects.get(pk=t.pk), True)  # the assignee completes it
    with pytest.raises(PermissionDenied):
        _clear(b, tl)
    assert _live(tl) == ["Given"]
    assert _clear(a, tl) == (1, 0) and _live(tl) == []


def test_other_lists_are_untouched(setup):
    a, b, tl = setup
    other = svc.create_list(a, "M")
    x, y = svc.create_task(a, tl, "X"), svc.create_task(a, other, "Y")
    _done(a, x)
    _done(a, y)
    _clear(a, tl)
    assert _live(other) == ["Y"]


def test_a_changed_set_or_version_deletes_nothing_and_the_old_token_is_refused(setup):
    a, b, tl = setup
    d1, d2 = svc.create_task(a, tl, "D1"), svc.create_task(a, tl, "D2")
    _done(a, d1)
    token = svc.plan_token(svc.clear_plan(tl))
    _done(a, d2)  # completed after the confirmation page was drawn
    n = AuditLog.objects.filter(action="tasks.list.clear_completed").count()
    with pytest.raises(svc.StaleVersion, match="changed since you looked"):
        svc.clear_completed(a, tl, token)
    assert _live(tl) == ["D1", "D2"] and AuditLog.objects.filter(action="tasks.list.clear_completed").count() == n
    token = svc.plan_token(svc.clear_plan(tl))
    svc.update_task(a, Task.objects.get(pk=d1.pk), Task.objects.get(pk=d1.pk).version, title="D1 edited")  # a version change alone
    with pytest.raises(svc.StaleVersion):
        svc.clear_completed(a, tl, token)
    svc.create_task(a, tl, "Late sub", parent=Task.objects.get(pk=d2.pk))  # a subtask added under a head: set differs
    token2 = svc.plan_token(svc.clear_plan(tl))
    with pytest.raises(svc.StaleVersion):
        svc.clear_completed(a, tl, token)
    with pytest.raises(svc.StaleVersion):
        svc.clear_completed(a, tl, "")
    assert _clear(a, tl, token2) == (1, 0)  # adding a subtask reopened D2 (the build default), so only D1 is completed
    assert _live(tl) == ["D2", "Late sub"]


def test_views_confirm_page_lists_heads_and_included_subtasks_then_clears(setup):
    a, b, tl = setup
    d, o = svc.create_task(a, tl, "Finished"), svc.create_task(a, tl, "Pending")
    kid = svc.create_task(a, tl, "OpenKid", parent=d)
    k = svc.create_task(a, tl, "DoneSub", parent=o)
    _done(a, d)
    _done(a, k)
    url = reverse("tasks:list", args=[tl.public_id])
    full = _c(a).get(url).content.decode()
    assert "Clear completed (2)" in full and "Hide completed" in full
    hid = _c(a).get(url + "?show=open").content.decode()
    assert "Pending" in hid and "Finished" not in hid and "DoneSub" not in hid and "Show completed" in hid and "Move Pending" not in hid
    assert _live(tl) == ["DoneSub", "Finished", "OpenKid", "Pending"]  # the filter deleted nothing
    clr = reverse("tasks:clear_completed", args=[tl.public_id])
    assert _c(b).get(clr).status_code == 404 and _c(b).post(clr).status_code == 404
    page = _c(a).get(clr).content.decode()
    assert "These 2 completed tasks" in page and "1 subtask" in page and "Finished" in page and "DoneSub" in page
    assert "OpenKid" in page and "included" in page
    assert _live(tl) == ["DoneSub", "Finished", "OpenKid", "Pending"]  # viewing deleted nothing
    token = svc.plan_token(svc.clear_plan(tl))
    svc.create_task(a, tl, "Fresh", parent=Task.objects.get(pk=o.pk))
    svc.set_status(a, Task.objects.get(pk=Task.objects.get(title="Fresh").pk), True)  # a new completed row after the page was drawn
    r = _c(a).post(clr, {"token": token}, follow=True)
    assert b"changed since you looked" in r.content and b"These 3 completed tasks" in r.content
    assert "Fresh" in _live(tl) and "Finished" in _live(tl)
    token = svc.plan_token(svc.clear_plan(tl))
    r = _c(a).post(clr, {"token": token}, follow=True)
    assert b"Moved 3 completed tasks and 1 included subtask to the Trash." in r.content and _live(tl) == ["Pending"]
    r = _c(a).get(clr, follow=True)
    assert b"no completed tasks to clear" in r.content and b"Clear completed" not in r.content


def test_list_page_query_count_does_not_grow_with_completed_tasks_and_the_count_matches_the_plan(setup, django_assert_max_num_queries):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    a, b, tl = setup
    url = reverse("tasks:list", args=[tl.public_id])

    def load():
        c = _c(a)
        c.get(url)  # warm session and caches
        with CaptureQueriesContext(connection) as q:
            body = c.get(url).content.decode()
        return len(q), body

    for i in range(2):
        t = svc.create_task(a, tl, f"H{i}")
        svc.create_task(a, tl, f"K{i}", parent=t)
        _done(a, t)
    few, body = load()
    assert "Clear completed (2)" in body
    for i in range(2, 8):
        t = svc.create_task(a, tl, f"H{i}")
        svc.create_task(a, tl, f"K{i}", parent=t)
        _done(a, t)
    many, body = load()
    assert "Clear completed (8)" in body and many == few  # 6 more completed heads, same number of queries
    assert svc.clear_plan(tl).__len__() == 8  # the button count equals what the review page lists
