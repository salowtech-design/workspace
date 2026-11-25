import logging

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import F, Max
from django.utils import timezone

from apps.core.foundation.audit import audit
from apps.core.foundation.notify import notify
from apps.core.foundation.permissions import require
from apps.tasks.models import NOTES_MAX, TITLE_MAX, Task, TaskList

logger = logging.getLogger(__name__)


def _clean_title(title):
    title = (title or "").strip()
    if not title:
        raise ValidationError("A task needs a title.")
    if len(title) > TITLE_MAX:
        raise ValidationError(f"Title is longer than {TITLE_MAX} characters.")
    return title


def _clean_notes(notes):
    notes = notes or ""
    if len(notes) > NOTES_MAX:
        raise ValidationError(f"Notes are longer than {NOTES_MAX} characters.")
    return notes


@transaction.atomic
def create_list(user, name):
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise ValidationError("A list needs a name of up to 120 characters.")
    tl = TaskList.objects.create(owner=user, name=name)
    audit(user, "tasks.list.create", "task_list", tl.pk)
    return tl


@transaction.atomic
def create_task(user, task_list, title, notes="", due_date=None, parent=None):
    require(user, "add", "task_list", task_list)
    title, notes = _clean_title(title), _clean_notes(notes)
    if parent is not None:
        # one level of subtasks, same list, and a task given to someone else cannot be a parent
        if parent.task_list_id != task_list.pk or parent.parent_id or parent.assignee_id:
            raise ValidationError(
                "A subtask must sit directly under a task of your own in the same list."
            )
        require(user, "edit", "task", parent)
    siblings = Task.objects.filter(task_list=task_list, parent=parent, deleted_at__isnull=True)
    pos = (siblings.aggregate(m=Max("position"))["m"] or 0) + 1
    t = Task.objects.create(
        task_list=task_list, parent=parent, title=title, notes=notes, due_date=due_date,
        created_by=user, position=pos,
    )  # fmt: skip
    audit(user, "tasks.task.create", "task", t.pk)
    return t


@transaction.atomic
def update_task(user, task, expected_version, **changes):
    """Edit title, notes or due date. Refuses when someone else changed the task first."""
    require(user, "edit", "task", task)
    fields = {}
    if "title" in changes:
        fields["title"] = _clean_title(changes["title"])
    if "notes" in changes:
        fields["notes"] = _clean_notes(changes["notes"])
    if "due_date" in changes:
        fields["due_date"] = changes["due_date"]
    done = Task.objects.filter(
        pk=task.pk, version=expected_version, deleted_at__isnull=True
    ).update(version=F("version") + 1, updated_at=timezone.now(), **fields)
    if not done:
        raise ValidationError("This task changed while you were editing. Reload and try again.")
    audit(user, "tasks.task.update", "task", task.pk, fields=sorted(fields))
    task.refresh_from_db()
    return task


@transaction.atomic
def set_status(user, task, completed):
    """Owner or assignee. Completing a parent completes its open subtasks; reopening a parent
    does not reopen them."""
    require(user, "complete", "task", task)
    now = timezone.now()
    ids = [task.pk]
    if completed and not task.parent_id:
        ids += list(
            task.subtasks.filter(deleted_at__isnull=True, status=Task.OPEN).values_list(
                "pk", flat=True
            )
        )
    Task.objects.filter(pk__in=ids, deleted_at__isnull=True).update(
        status=Task.DONE if completed else Task.OPEN,
        completed_at=now if completed else None,
        version=F("version") + 1,
        updated_at=now,
    )
    audit(user, "tasks.task.complete" if completed else "tasks.task.reopen", "task", task.pk)
    task.refresh_from_db()
    return task


@transaction.atomic
def assign(user, task, person):
    """Give a top-level task to one active colleague (or pass None to take it back)."""
    require(user, "assign", "task", task)
    if person is not None:
        if task.parent_id or task.subtasks.filter(deleted_at__isnull=True).exists():
            raise ValidationError("Only a task without subtasks can be assigned.")
        if not person.is_active or person.status != "active":
            raise ValidationError("That person is not active.")
    Task.objects.filter(pk=task.pk).update(
        assignee=person, version=F("version") + 1, updated_at=timezone.now()
    )
    audit(user, "tasks.task.assign", "task", task.pk, assignee=getattr(person, "pk", None))
    task.refresh_from_db()
    if person is not None and person.pk != user.pk:
        try:
            with transaction.atomic():
                notify(
                    person,
                    "tasks.assigned",
                    f"{user.full_name or user.employee_id} gave you a task",
                    "/tasks/assigned/",
                    check=("task", task),
                )
        except DatabaseError:
            raise
        except Exception:  # noqa: BLE001 - a failed notice never undoes the assignment
            logger.exception("task assignment notification skipped")
    return task


@transaction.atomic
def delete_task(user, task):
    require(user, "delete", "task", task)
    now = timezone.now()
    Task.objects.filter(pk__in=[task.pk, *task.subtasks.values_list("pk", flat=True)]).update(
        deleted_at=now, updated_at=now, version=F("version") + 1
    )
    audit(user, "tasks.task.delete", "task", task.pk)


@transaction.atomic
def delete_list(user, task_list):
    require(user, "delete", "task_list", task_list)
    now = timezone.now()
    task_list.deleted_at = now
    task_list.save(update_fields=["deleted_at"])
    Task.objects.filter(task_list=task_list, deleted_at__isnull=True).update(deleted_at=now)
    audit(user, "tasks.list.delete", "task_list", task_list.pk)


def lists_for(user):
    return TaskList.objects.filter(owner=user, deleted_at__isnull=True).order_by("created_at")


def tasks_in(user, task_list):
    require(user, "view", "task_list", task_list)
    return Task.objects.filter(task_list=task_list, deleted_at__isnull=True).order_by(
        "parent_id", "position", "id"
    )


def assigned_to(user):
    return (
        Task.objects.filter(
            assignee=user, deleted_at__isnull=True, task_list__deleted_at__isnull=True
        )
        .select_related("task_list__owner")
        .order_by("status", "due_date", "id")
    )


def get_task(user, public_id, action="view"):
    """Fetch by public id; anyone not allowed gets the same not-found as a missing task."""
    from django.http import Http404

    t = Task.objects.filter(public_id=public_id).select_related("task_list").first()
    from apps.core.foundation.permissions import can

    if t is None or not can(user, action, "task", t):
        raise Http404
    return t
