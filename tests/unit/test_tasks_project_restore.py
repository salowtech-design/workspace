import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from apps.core.models import AuditLog
from apps.tasks import projects as pj
from apps.tasks.models import ProjectTask
from tests.unit.test_tasks_projects import _c, _member, _u


@pytest.fixture
def board(db):
    lead, member, stranger = _u("PR-L"), _u("PR-M"), _u("PR-S")
    p = pj.create_project(lead, "Restore me")
    _member(p, member)
    col = p.columns.first()
    return lead, member, stranger, p, col


def test_lead_restores_to_the_end_of_its_column_with_fields_kept_and_a_version_bump(board):
    lead, member, stranger, p, col = board
    first = pj.add_task(lead, p, col.pk, "First", notes="keep me")
    later = pj.add_task(lead, p, col.pk, "Later")
    ms = pj.create_milestone(lead, p, "M1")
    pj.set_milestone(lead, ProjectTask.objects.get(pk=first.pk), ms.pk)
    pj.delete_task(lead, ProjectTask.objects.get(pk=first.pk))
    v = ProjectTask.objects.get(pk=first.pk).version
    assert [t.title for t in pj.deleted_tasks(lead, p)] == ["First"]
    pj.restore_task(lead, ProjectTask.objects.get(pk=first.pk))
    row = ProjectTask.objects.get(pk=first.pk)
    assert row.deleted_at is None and row.version == v + 1 and row.notes == "keep me" and row.milestone_id == ms.pk
    assert row.position > ProjectTask.objects.get(pk=later.pk).position  # the end of the column
    assert pj.deleted_tasks(lead, p) == []
    assert AuditLog.objects.filter(action="tasks.project_task.restore").count() == 1


def test_a_form_opened_before_the_delete_is_stale_after_restore(board):
    lead, member, stranger, p, col = board
    t = pj.add_task(lead, p, col.pk, "Old form")
    seen = ProjectTask.objects.get(pk=t.pk).version
    pj.delete_task(lead, ProjectTask.objects.get(pk=t.pk))
    pj.restore_task(lead, ProjectTask.objects.get(pk=t.pk))
    from apps.tasks.services import StaleVersion

    with pytest.raises(StaleVersion):
        pj.edit_task(lead, ProjectTask.objects.get(pk=t.pk), seen, title="x")


def test_member_stranger_live_task_and_deleted_project_are_refused(board):
    lead, member, stranger, p, col = board
    t = pj.add_task(lead, p, col.pk, "T")
    with pytest.raises(ValidationError, match="not in the trash"):
        pj.restore_task(lead, ProjectTask.objects.get(pk=t.pk))
    pj.delete_task(lead, ProjectTask.objects.get(pk=t.pk))
    with pytest.raises(PermissionDenied):
        pj.restore_task(member, ProjectTask.objects.get(pk=t.pk))  # a plain member has no delete right, so no restore right
    with pytest.raises(PermissionDenied):
        pj.deleted_tasks(member, p)
    with pytest.raises(Http404):
        pj.restore_task(stranger, ProjectTask.objects.get(pk=t.pk))
    assert ProjectTask.objects.get(pk=t.pk).deleted_at is not None
    from apps.tasks.models import Project

    Project.objects.filter(pk=p.pk).update(deleted_at=ProjectTask.objects.get(pk=t.pk).deleted_at)
    with pytest.raises(Http404):
        pj.restore_task(lead, ProjectTask.objects.get(pk=t.pk))


def test_views_trash_is_lead_only_and_restore_returns_to_the_board(board):
    from django.urls import reverse

    lead, member, stranger, p, col = board
    t = pj.add_task(lead, p, col.pk, "Gone task")
    pj.delete_task(lead, ProjectTask.objects.get(pk=t.pk))
    trash = reverse("tasks:project_trash", args=[p.public_id])
    restore = reverse("tasks:project_restore", args=[t.public_id])
    assert _c(stranger).get(trash).status_code == 404
    assert _c(member).get(trash).status_code == 404
    assert _c(stranger).post(restore).status_code == 404
    assert _c(member).post(restore).status_code == 404
    assert _c(lead).get(restore).status_code == 405
    r = _c(lead).get(trash)
    assert r.status_code == 200 and b"Gone task" in r.content and b"Restore" in r.content
    r = _c(lead).post(restore, follow=True)
    assert b"Task restored." in r.content and ProjectTask.objects.get(pk=t.pk).deleted_at is None
    r = _c(lead).post(restore, follow=True)  # now live: a plain message, nothing changes
    assert b"not in the trash" in r.content
    assert b"Project trash" in _c(lead).get(reverse("tasks:project", args=[p.public_id])).content
    assert b"Project trash" not in _c(member).get(reverse("tasks:project", args=[p.public_id])).content


def test_a_restored_task_is_not_shown_to_an_assignee_who_left_the_project(board):
    """Source facts: assigned_in_projects gates on a live membership (removed_at empty, project live). Nothing in the app sets removed_at, so the
    fixture sets it directly. The former member must not see the restored title, and the lead's board still shows the task."""
    from django.urls import reverse

    from apps.tasks.models import ProjectMember

    lead, member, stranger, p, col = board
    t = pj.add_task(lead, p, col.pk, "Secret plan")
    pj.assign_task(lead, ProjectTask.objects.get(pk=t.pk), member)
    assigned = reverse("tasks:assigned")
    assert b"Secret plan" in _c(member).get(assigned).content  # a current member sees it
    pj.delete_task(lead, ProjectTask.objects.get(pk=t.pk))
    from django.utils import timezone

    ProjectMember.objects.filter(project=p, person=member).update(removed_at=timezone.now())
    pj.restore_task(lead, ProjectTask.objects.get(pk=t.pk))
    assert ProjectTask.objects.get(pk=t.pk).assignee_id == member.pk  # the assignee field is kept
    assert b"Secret plan" not in _c(member).get(assigned).content
    assert _c(member).get(reverse("tasks:project", args=[p.public_id])).status_code == 404
    assert b"Secret plan" in _c(lead).get(reverse("tasks:project", args=[p.public_id])).content
