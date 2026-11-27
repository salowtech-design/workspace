import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog
from apps.tasks import services as svc
from apps.tasks.models import Task
from tests.unit.test_tasks_services import _u


@pytest.fixture
def setup(db):
    a, b = _u("R-A"), _u("R-B")
    return a, b, svc.create_list(a, "Work")


def _c(u):
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(u)
    return c


def test_restore_a_task_brings_back_its_subtasks_and_its_assignee_and_bumps_the_version(setup):
    a, b, tl = setup
    p = svc.create_task(a, tl, "Parent")
    kid = svc.create_task(a, tl, "Kid", parent=p)
    old = svc.create_task(a, tl, "Deleted earlier", parent=p)
    svc.delete_task(a, old)  # its own, earlier deletion
    v0 = Task.objects.get(pk=p.pk).version
    svc.delete_task(a, p)
    lists, tasks = svc.trash_for(a)
    assert [t.title for t in tasks] == ["Parent", "Deleted earlier"] and lists == []  # Kid went with Parent: not listed; the earlier one is
    svc.restore_task(a, Task.objects.get(pk=p.pk))
    live = {t.title for t in svc.tasks_in(a, tl)}
    assert live == {"Parent", "Kid"}  # the earlier deletion stays deleted
    assert Task.objects.get(pk=p.pk).version == v0 + 2 and Task.objects.get(pk=kid.pk).deleted_at is None
    solo = svc.create_task(a, tl, "Given away")
    svc.assign(a, solo, b)
    svc.delete_task(a, solo)
    assert svc.assigned_to(b).count() == 0
    svc.restore_task(a, Task.objects.get(pk=solo.pk))
    assert [t.title for t in svc.assigned_to(b)] == ["Given away"]  # the assignee is back
    assert AuditLog.objects.filter(action="tasks.task.restore").count() == 2  # Parent, then Given away


def test_only_the_owner_restores_and_a_live_task_or_a_deleted_list_refuses(setup):
    a, b, tl = setup
    t = svc.create_task(a, tl, "T")
    svc.assign(a, t, b)
    with pytest.raises(Exception):
        svc.restore_task(a, t)  # not deleted
    svc.delete_task(a, t)
    t = Task.objects.get(pk=t.pk)
    with pytest.raises(Exception):
        svc.restore_task(b, t)  # the assignee has no right, deleted or not
    assert Task.objects.get(pk=t.pk).deleted_at is not None
    svc.delete_list(a, tl)
    with pytest.raises(Exception):
        svc.restore_task(a, Task.objects.get(pk=t.pk))  # its list is deleted: restore the list
    assert svc.trash_for(a)[1] == []  # tasks of a deleted list are not listed by themselves


def test_a_subtask_refuses_while_its_parent_is_deleted_and_restores_after(setup):
    a, b, tl = setup
    p = svc.create_task(a, tl, "P")
    k = svc.create_task(a, tl, "K", parent=p)
    svc.delete_task(a, k)
    svc.delete_task(a, p)
    with pytest.raises(ValidationError):
        svc.restore_task(a, Task.objects.get(pk=k.pk))
    assert Task.objects.get(pk=k.pk).deleted_at is not None
    svc.restore_task(a, Task.objects.get(pk=p.pk))
    assert {t.title for t in svc.tasks_in(a, tl)} == {"P"}  # K was deleted at another moment: still in the trash
    svc.restore_task(a, Task.objects.get(pk=k.pk))
    assert {t.title for t in svc.tasks_in(a, tl)} == {"P", "K"}


def test_restore_a_list_brings_back_only_the_tasks_deleted_with_it(setup):
    a, b, tl = setup
    keep = svc.create_task(a, tl, "Keep")
    gone = svc.create_task(a, tl, "Gone earlier")
    svc.delete_task(a, gone)
    svc.delete_list(a, tl)
    lists, tasks = svc.trash_for(a)
    assert [x.name for x in lists] == ["Work"] and tasks == []
    with pytest.raises(Exception):
        svc.restore_list(b, tl)  # not the owner
    svc.restore_list(a, tl)
    assert [x.name for x in svc.lists_for(a)] == ["Work"]
    assert {t.title for t in svc.tasks_in(a, tl)} == {"Keep"}
    assert [t.title for t in svc.trash_for(a)[1]] == ["Gone earlier"]
    assert Task.objects.get(pk=keep.pk).deleted_at is None
    with pytest.raises(Exception):
        svc.restore_list(a, tl)  # a live list is not in the trash


def test_pages_show_only_the_owners_trash_and_restore_by_post(setup):
    a, b, tl = setup
    t = svc.create_task(a, tl, "Visible only to its owner")
    svc.delete_task(a, t)
    ca, cb = _c(a), _c(b)
    page = ca.get(reverse("tasks:trash")).content.decode()
    assert "Visible only to its owner" in page and "Restore" in page
    assert "Visible only to its owner" not in cb.get(reverse("tasks:trash")).content.decode()  # not B's trash
    url = reverse("tasks:restore_task", args=[t.public_id])
    assert cb.post(url).status_code == 404  # a stranger learns nothing
    assert Task.objects.get(pk=t.pk).deleted_at is not None
    assert ca.get(url).status_code == 405  # GET never restores
    assert ca.post(url).status_code == 302
    assert Task.objects.get(pk=t.pk).deleted_at is None
    assert "Visible only to its owner" not in ca.get(reverse("tasks:trash")).content.decode()
    assert "restore it from the Trash" in ca.get(reverse("tasks:delete", args=[t.public_id])).content.decode()
    svc.delete_list(a, tl)
    lurl = reverse("tasks:restore_list", args=[tl.public_id])
    assert cb.post(lurl).status_code == 404 and ca.post(lurl).status_code == 302
    assert [x.name for x in svc.lists_for(a)] == ["Work"]


def test_restoring_a_subtask_keeps_the_subtask_rules(setup):
    a, b, tl = setup
    p = svc.create_task(a, tl, "P")
    k = svc.create_task(a, tl, "K", parent=p)
    svc.delete_task(a, k)
    svc.assign(a, p, b)  # allowed now: no live subtasks
    with pytest.raises(ValidationError):
        svc.restore_task(a, Task.objects.get(pk=k.pk))  # a task given to someone cannot hold subtasks
    assert Task.objects.get(pk=k.pk).deleted_at is not None
    svc.assign(a, p, None)
    svc.set_status(a, Task.objects.get(pk=p.pk), True)  # parent completed
    assert Task.objects.get(pk=p.pk).status == Task.DONE
    svc.restore_task(a, Task.objects.get(pk=k.pk))  # an open subtask comes back
    assert Task.objects.get(pk=p.pk).status == Task.OPEN  # the parent opens again, as when a subtask is added


def test_a_form_opened_before_the_delete_is_stale_after_restore_and_a_deleted_task_cannot_be_assigned(setup):
    a, b, tl = setup
    t = svc.create_task(a, tl, "Old form")
    seen = Task.objects.get(pk=t.pk).version  # the version an edit form was opened at
    svc.delete_task(a, t)
    with pytest.raises(Exception):
        svc.assign(a, Task.objects.get(pk=t.pk), b)  # deleted: no assigning, no editing
    with pytest.raises(Exception):
        svc.update_task(a, Task.objects.get(pk=t.pk), seen, title="x")
    svc.restore_task(a, Task.objects.get(pk=t.pk))
    with pytest.raises(svc.StaleVersion):
        svc.update_task(a, Task.objects.get(pk=t.pk), seen, title="late edit")
    assert Task.objects.get(pk=t.pk).title == "Old form"
    svc.update_task(a, Task.objects.get(pk=t.pk), Task.objects.get(pk=t.pk).version, title="fresh edit")  # the current version works
    assert Task.objects.get(pk=t.pk).title == "fresh edit"


def test_deletions_in_separate_calls_have_separate_moments(setup):
    a, b, tl = setup
    p = svc.create_task(a, tl, "P")
    ks = [svc.create_task(a, tl, f"K{i}", parent=p) for i in range(3)]
    for k in ks:
        svc.delete_task(a, k)  # three separate deletions under one parent
    moments = {Task.objects.get(pk=k.pk).deleted_at for k in ks}
    assert len(moments) == 3  # distinct, so restoring the parent's delete cannot match them
    svc.delete_task(a, p)
    svc.restore_task(a, Task.objects.get(pk=p.pk))
    assert [t.title for t in svc.tasks_in(a, tl)] == ["P"]  # none of the earlier three revived
