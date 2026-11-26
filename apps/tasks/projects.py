"""Projects, stage 2 first step: create, see, read the member list, read the board.
Member add/remove, project tasks and milestones are NOT built: what membership reveals and who
may add people is an open policy choice. Nothing here notifies anyone."""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Max
from django.db.models.functions import Lower
from django.http import Http404
from django.utils import timezone

from apps.core.foundation.audit import audit
from apps.tasks.models import Column, Milestone, Project, ProjectMember, ProjectTask
from apps.tasks.services import StaleVersion, _clean_notes, _clean_title, _due

DEFAULT_COLUMNS = ("To do", "Doing", "Done")  # build default, not policy


def _membership(user, project):
    return ProjectMember.objects.filter(
        project=project, person=user, removed_at__isnull=True, person__is_active=True
    ).first()


def can_view(user, project):
    return project.deleted_at is None and _membership(user, project) is not None


@transaction.atomic
def create_project(user, name):
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise ValidationError("A project needs a name of up to 120 characters.")
    p = Project.objects.create(name=name, created_by=user)
    ProjectMember.objects.create(project=p, person=user, role=ProjectMember.LEAD, added_by=user)
    Column.objects.bulk_create(
        [Column(project=p, name=n, position=i) for i, n in enumerate(DEFAULT_COLUMNS)]
    )
    audit(user, "tasks.project.create", "project", p.pk)
    return p


def projects_for(user):
    return Project.objects.filter(
        deleted_at__isnull=True,
        members__person=user,
        members__removed_at__isnull=True,
    ).order_by("name", "id")


def get_project(user, public_id):
    """Anyone who is not a current member gets the same not-found as a missing project."""
    p = Project.objects.filter(public_id=public_id).first()
    if p is None or not can_view(user, p):
        raise Http404
    return p


def members_of(project):
    return (
        ProjectMember.objects.filter(project=project, removed_at__isnull=True)
        .select_related("person")
        .order_by("-role", "person__full_name", "id")
    )


def _lock_project(project):
    return Project.objects.select_for_update().get(pk=project.pk)


def _lock_as_lead(user, project):
    """Project row first, then judge membership on fresh data under that lock. Only a lead
    changes project tasks in this step (build default; wider roles are an open choice)."""
    p = _lock_project(project)
    m = _membership(user, p) if p.deleted_at is None else None
    if m is None:
        raise Http404
    if m.role != ProjectMember.LEAD:
        raise PermissionDenied
    return p


def _pause():
    """Test hook, no-op."""


@transaction.atomic
def add_task(user, project, column_id, title, notes="", due=None):
    p = _lock_as_lead(user, project)
    col = Column.objects.filter(pk=column_id, project=p).first()
    if col is None:
        raise ValidationError("Pick a column of this project.")
    title, notes = _clean_title(title), _clean_notes(notes)
    _pause()
    top = ProjectTask.objects.filter(column=col, deleted_at__isnull=True).aggregate(
        m=Max("position")
    )["m"]
    t = ProjectTask.objects.create(
        project=p,
        column=col,
        title=title,
        notes=notes,
        due_date=due,
        position=0 if top is None else top + 1,
        created_by=user,
    )
    audit(user, "tasks.project_task.create", "project_task", t.pk, project=p.pk)
    return t


def _fresh_task(p, task):
    t = ProjectTask.objects.filter(pk=task.pk, project=p, deleted_at__isnull=True).first()
    if t is None:
        raise Http404
    return t


@transaction.atomic
def move_task(user, task, column_id, expected_version):
    p = _lock_as_lead(user, task.project)
    t = _fresh_task(p, task)
    col = Column.objects.filter(pk=column_id, project=p).first()
    if col is None:
        raise ValidationError("Pick a column of this project.")
    if t.column_id == col.pk:
        return t  # no-op: no version bump, no audit
    top = ProjectTask.objects.filter(column=col, deleted_at__isnull=True).aggregate(
        m=Max("position")
    )["m"]
    done = ProjectTask.objects.filter(pk=t.pk, version=expected_version).update(
        column=col,
        position=0 if top is None else top + 1,
        version=F("version") + 1,
        updated_at=timezone.now(),
    )
    if not done:
        raise StaleVersion("This task changed while you were looking at it.")
    audit(user, "tasks.project_task.move", "project_task", t.pk, to_column=col.pk)
    t.refresh_from_db()
    return t


@transaction.atomic
def assign_task(user, task, person, expected_version=None):
    """Only a current member of this project can be assigned, so assigning never shows the
    project to a new person. No notice is sent."""
    p = _lock_as_lead(user, task.project)
    t = _fresh_task(p, task)
    if person is not None and not (person.is_active and _membership(person, p) is not None):
        raise ValidationError("That person is not a member of this project.")
    if t.assignee_id == getattr(person, "pk", None):
        return t  # nothing changes: no version bump, no audit (a stale version is not checked)
    _pause()
    q = ProjectTask.objects.filter(pk=t.pk)
    if expected_version is not None:
        q = q.filter(version=expected_version)
    if not q.update(assignee=person, version=F("version") + 1, updated_at=timezone.now()):
        raise StaleVersion("This task changed while you were looking at it.")
    audit(
        user,
        "tasks.project_task.assign",
        "project_task",
        t.pk,
        assignee=getattr(person, "pk", None),
    )
    t.refresh_from_db()
    return t


def tasks_by_column(project, q="", milestone=None, overdue_before=None):
    """Board rows by column. Filters narrow what is shown and never change data:
    q = title contains (case-insensitive), milestone = a Milestone pk of this project or
    "none" (tasks with no milestone), overdue_before = a date: due before it and not in Done."""
    rows = ProjectTask.objects.filter(project=project, deleted_at__isnull=True)
    if q:
        # Lower() on both sides: icontains relied on the column collation, which is
        # case-sensitive on the real TiDB test store (found by the real-stack run).
        rows = rows.annotate(_lt=Lower("title")).filter(_lt__contains=q.lower())
    if milestone == "none":
        rows = rows.filter(milestone__isnull=True)
    elif milestone is not None:
        rows = rows.filter(milestone_id=milestone, milestone__project=project)
    if overdue_before is not None:
        rows = rows.filter(due_date__lt=overdue_before).exclude(column__name="Done")
    rows = rows.select_related("milestone").order_by("position", "id")
    out = {}
    for t in rows:
        out.setdefault(t.column_id, []).append(t)
    return out


def get_task(user, public_id):
    t = (
        ProjectTask.objects.filter(public_id=public_id, deleted_at__isnull=True)
        .select_related("project")
        .first()
    )
    if t is None or not can_view(user, t.project):
        raise Http404
    return t


_EDITABLE = {"title", "notes", "due_date"}


@transaction.atomic
def edit_task(user, task, expected_version, **changes):
    unknown = set(changes) - _EDITABLE
    if unknown:
        raise ValidationError("That field cannot be edited.")
    p = _lock_as_lead(user, task.project)
    t = _fresh_task(p, task)
    if not changes:
        return t  # nothing sent: no version bump, no audit
    fields = {}
    if "title" in changes:
        fields["title"] = _clean_title(changes["title"])
    if "notes" in changes:
        fields["notes"] = _clean_notes(changes["notes"])
    if "due_date" in changes:
        fields["due_date"] = _due(changes["due_date"])
    _pause()
    done = ProjectTask.objects.filter(pk=t.pk, version=expected_version).update(
        version=F("version") + 1, updated_at=timezone.now(), **fields
    )
    if not done:
        raise StaleVersion("This task changed while you were editing.")
    audit(user, "tasks.project_task.update", "project_task", t.pk, fields=sorted(fields))
    t.refresh_from_db()
    return t


@transaction.atomic
def delete_task(user, task):
    """Soft delete under the project lock. Nothing is erased and no restore exists yet."""
    p = _lock_as_lead(user, task.project)
    t = _fresh_task(p, task)
    _pause()
    ProjectTask.objects.filter(pk=t.pk, deleted_at__isnull=True).update(
        deleted_at=timezone.now(), updated_at=timezone.now()
    )
    audit(user, "tasks.project_task.delete", "project_task", t.pk, project=p.pk)


@transaction.atomic
def create_milestone(user, project, name, due=None):
    p = _lock_as_lead(user, project)
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise ValidationError("A milestone needs a name of up to 120 characters.")
    if due is not None and not isinstance(due, datetime.date):
        raise ValidationError("Use a date like 2026-12-31.")
    m = Milestone.objects.create(project=p, name=name, due_date=due, created_by=user)
    audit(user, "tasks.milestone.create", "milestone", m.pk, project=p.pk)
    return m


@transaction.atomic
def set_milestone(user, task, milestone_id, expected_version=None):
    """Point a task at a milestone of its own project, or clear it (empty id)."""
    p = _lock_as_lead(user, task.project)
    t = _fresh_task(p, task)
    ms = None
    if milestone_id not in (None, ""):
        ms = Milestone.objects.filter(pk=milestone_id, project=p, deleted_at__isnull=True).first()
        if ms is None:
            raise ValidationError("Pick a milestone of this project.")
    if t.milestone_id == getattr(ms, "pk", None):
        return t  # no-op: no version bump, no audit
    _pause()
    q = ProjectTask.objects.filter(pk=t.pk)
    if expected_version is not None:
        q = q.filter(version=expected_version)
    if not q.update(milestone=ms, version=F("version") + 1, updated_at=timezone.now()):
        raise StaleVersion("This task changed while you were looking at it.")
    audit(
        user,
        "tasks.project_task.milestone",
        "project_task",
        t.pk,
        milestone=getattr(ms, "pk", None),
    )
    t.refresh_from_db()
    return t


def milestones_of(project):
    from django.db.models import Count, Q

    return (
        Milestone.objects.filter(project=project, deleted_at__isnull=True)
        .annotate(
            total=Count("tasks", filter=Q(tasks__deleted_at__isnull=True)),
            in_done=Count(
                "tasks", filter=Q(tasks__deleted_at__isnull=True, tasks__column__name="Done")
            ),
        )
        .order_by("due_date", "id")
    )


COMMENTS_SHOWN = 100


def comments_of(task, before=None):
    """One page of up to COMMENTS_SHOWN comments, oldest first. `before` is a comment id: the page
    is the comments just before it, so every older comment can be reached. Returns
    (rows, older_cursor); older_cursor is the id to pass as `before` for the page before this one,
    or None when this page starts at the first comment."""
    from apps.tasks.models import TaskComment

    qs = TaskComment.objects.filter(task=task)
    if before is not None:
        qs = qs.filter(pk__lt=before)
    rows = list(qs.select_related("author").order_by("-pk")[: COMMENTS_SHOWN + 1])
    more = len(rows) > COMMENTS_SHOWN
    rows = rows[:COMMENTS_SHOWN]
    rows.reverse()
    return rows, (rows[0].pk if more and rows else None)


@transaction.atomic
def add_comment(user, task, body):
    """Any current member may comment on a task they can see. Plain text, trimmed."""
    from apps.tasks.models import COMMENT_MAX, TaskComment

    body = (body or "").strip()
    if not body:
        raise ValidationError("Write something before posting.")
    if len(body) > COMMENT_MAX:
        raise ValidationError(f"Comments can be up to {COMMENT_MAX} characters.")
    task = ProjectTask.objects.select_for_update().get(pk=task.pk)
    if task.deleted_at is not None or not can_view(user, task.project):
        raise Http404
    c = TaskComment.objects.create(task=task, author=user, body=body)
    audit(user, "tasks.project.comment", "project_task", task.pk, comment=c.pk)
    return c
