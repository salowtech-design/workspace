import datetime
import logging
import uuid

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.core.foundation.audit import audit
from apps.core.foundation.notify import notify
from apps.core.foundation.permissions import require
from apps.tasks.models import NOTES_MAX, TITLE_MAX, Task, TaskList

logger = logging.getLogger(__name__)


class StaleVersion(ValidationError):
    """The task was changed (or removed) since the form was loaded."""


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
def rename_list(user, task_list, name):
    """The owner renames a list. Same name rule as creating one; no uniqueness rule, as there is none for creating."""
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise ValidationError("A list needs a name of up to 120 characters.")
    tl = _lock_list(task_list)
    require(user, "edit", "task_list", tl)
    if tl.name != name:
        TaskList.objects.filter(pk=tl.pk).update(name=name)
        audit(user, "tasks.list.rename", "task_list", tl.pk)
    tl.refresh_from_db()
    return tl


def _pause():
    """Test hook, called after a mutation has checked its rules and before it writes."""


def _lock_list(task_list):
    """Every mutation takes this first. One lock per list serialises all changes to the list
    and its tasks, and returns the list as it is NOW so permission is judged on fresh data."""
    return TaskList.objects.select_for_update().get(pk=task_list.pk)


def _fresh(task):
    return Task.objects.select_related("task_list").get(pk=task.pk)


def _refuse_if_moved(fresh, locked_list):
    """A move may have committed while this call waited on the list lock it took from a stale view of the task. The task then sits in another
    list that this call does not hold: refuse, so the right and the write are never judged under the wrong list's lock."""
    if fresh.task_list_id != locked_list.pk:
        raise StaleVersion("This task was moved to another list. Reload and try again.")


def _locked_task(user, task, action):
    """Lock the list, re-read the task under the lock, then check permission on the fresh
    rows. A task or list deleted or reassigned a moment ago is refused, not acted on."""
    tl = _lock_list(task.task_list)  # list first, always
    fresh = _fresh(task)
    _refuse_if_moved(fresh, tl)
    fresh.task_list = tl
    require(user, action, "task", fresh)
    return fresh


def _due(value):
    if (
        value is None
        or isinstance(value, datetime.date)
        and not isinstance(value, datetime.datetime)
    ):
        return value
    raise ValidationError("A due date must be a date.")


@transaction.atomic
def create_task(user, task_list, title, notes="", due_date=None, parent=None):
    tl = _lock_list(task_list)
    require(user, "add", "task_list", tl)
    title, notes, due_date = _clean_title(title), _clean_notes(notes), _due(due_date)
    if parent is not None:
        parent = _fresh(parent)
        # one level of subtasks, same list, and a task given to someone else cannot be a parent
        if (
            parent.deleted_at is not None
            or parent.task_list_id != tl.pk
            or parent.parent_id
            or parent.assignee_id
        ):
            raise ValidationError(
                "A subtask must sit directly under a task of your own in the same list."
            )
        parent.task_list = tl  # the list as locked just now
        require(user, "edit", "task", parent)
    siblings = Task.objects.filter(task_list=tl, parent=parent, deleted_at__isnull=True)
    pos = (siblings.aggregate(m=Max("position"))["m"] or 0) + 1
    _pause()
    t = Task.objects.create(
        task_list=tl, parent=parent, title=title, notes=notes, due_date=due_date,
        created_by=user, position=pos,
    )  # fmt: skip
    if parent is not None and parent.status == Task.DONE:
        # build default (pending the blueprint): a completed task never holds an open subtask
        Task.objects.filter(pk=parent.pk).update(
            status=Task.OPEN, completed_at=None, version=F("version") + 1, updated_at=timezone.now()
        )
    audit(user, "tasks.task.create", "task", t.pk)
    return t


_EDITABLE = {"title", "notes", "due_date"}


@transaction.atomic
def update_task(user, task, expected_version, **changes):
    """Edit title, notes or due date. Refuses when someone else changed the task first."""
    unknown = set(changes) - _EDITABLE
    if unknown:
        raise ValidationError("That field cannot be edited.")
    task = _locked_task(user, task, "edit")
    fields = {}
    if not changes:
        return task  # nothing sent: no version bump, no audit row
    if "title" in changes:
        fields["title"] = _clean_title(changes["title"])
    if "notes" in changes:
        fields["notes"] = _clean_notes(changes["notes"])
    if "due_date" in changes:
        fields["due_date"] = _due(changes["due_date"])
    done = Task.objects.filter(
        pk=task.pk, version=expected_version, deleted_at__isnull=True
    ).update(version=F("version") + 1, updated_at=timezone.now(), **fields)
    if not done:
        raise StaleVersion("This task changed while you were editing.")
    audit(user, "tasks.task.update", "task", task.pk, fields=sorted(fields))
    task.refresh_from_db()
    return task


@transaction.atomic
def set_status(user, task, completed):
    """Owner or assignee. Build defaults, pending the blueprint: completing a parent completes
    its open subtasks; reopening a subtask reopens a completed parent; a repeat of the current
    state changes nothing and writes nothing."""
    task = _locked_task(user, task, "complete")
    want = Task.DONE if completed else Task.OPEN
    if task.status == want:
        return task
    now = timezone.now()
    ids, cascaded = [task.pk], 0
    if completed and not task.parent_id:
        kids = list(
            task.subtasks.filter(deleted_at__isnull=True, status=Task.OPEN).values_list(
                "pk", flat=True
            )
        )
        ids += kids
        cascaded = len(kids)
    if not completed and task.parent_id:
        ids.append(task.parent_id)
    _pause()
    Task.objects.filter(pk__in=ids, deleted_at__isnull=True).exclude(status=want).update(
        status=want,
        completed_at=now if completed else None,
        version=F("version") + 1,
        updated_at=now,
    )
    audit(
        user, "tasks.task.complete" if completed else "tasks.task.reopen", "task", task.pk,
        subtasks_changed=cascaded or None,
    )  # fmt: skip
    task.refresh_from_db()
    return task


@transaction.atomic
def assign(user, task, person):
    """Give a top-level task to one active colleague (or pass None to take it back).
    Giving it to the person who already has it changes nothing and sends no second notice."""
    task = _locked_task(user, task, "assign")
    if (getattr(person, "pk", None)) == task.assignee_id:
        return task
    if person is not None:
        if task.parent_id or task.subtasks.filter(deleted_at__isnull=True).exists():
            raise ValidationError("Only a task without subtasks can be assigned.")
        person = CustomUser.objects.get(pk=person.pk)  # fresh: status may have changed
        if not person.is_active or person.status != "active":
            raise ValidationError("That person is not active.")
    _pause()
    Task.objects.filter(pk=task.pk).update(
        assignee=person, version=F("version") + 1, updated_at=timezone.now()
    )
    audit(user, "tasks.task.assign", "task", task.pk, assignee=getattr(person, "pk", None))
    task.refresh_from_db()
    if person is not None and person.pk != user.pk:
        try:
            with transaction.atomic():
                notify(
                    person, "tasks.assigned", f"{user.full_name or user.employee_id} gave you a task",
                    "/tasks/assigned/", check=("task", task),
                )  # fmt: skip
        except DatabaseError:
            raise
        except Exception:  # noqa: BLE001 - a failed notice never undoes the assignment
            logger.exception("task assignment notification skipped")
    return task


@transaction.atomic
def move_task(user, task, expected_version, target_list):
    """The owner moves a top-level task, with all its subtasks (deleted ones too, so a later restore still finds the parent in its own list), to
    the end of another list of their own. A subtask alone is refused: it must stay in its parent's list. An assigned task keeps its assignee, whose
    right comes from the assignment and not the list. Both lists are locked in primary-key order so two opposite moves cannot deadlock; the task
    is re-read under the locks and a changed version or list is refused as stale."""
    source_id = Task.objects.values_list("task_list_id", flat=True).get(pk=task.pk)
    locked = {tl.pk: tl for tl in TaskList.objects.select_for_update().filter(pk__in={source_id, target_list.pk}).order_by("pk")}
    source, target = locked[source_id], locked[target_list.pk]
    fresh = _fresh(task)
    if fresh.task_list_id != source_id or fresh.version != expected_version:
        raise StaleVersion("This task changed while you were working.")
    fresh.task_list = source
    require(user, "edit", "task", fresh)
    require(user, "add", "task_list", target)
    if source.pk == target.pk:
        return fresh
    if fresh.parent_id:
        raise ValidationError("A subtask stays in its parent task's list. Move the parent task instead.")
    pos = (Task.objects.filter(task_list=target, parent=None, deleted_at__isnull=True).aggregate(m=Max("position"))["m"] or 0) + 1
    _pause()
    now = timezone.now()
    kids = Task.objects.filter(parent=fresh).update(task_list=target, version=F("version") + 1, updated_at=now)
    Task.objects.filter(pk=fresh.pk).update(task_list=target, position=pos, version=F("version") + 1, updated_at=now)
    audit(user, "tasks.task.move", "task", fresh.pk, to=target.pk, subtasks_moved=kids or None)
    fresh.refresh_from_db()
    return fresh


@transaction.atomic
def delete_task(user, task):
    task = _locked_task(user, task, "delete")
    now = timezone.now()
    kids = list(task.subtasks.filter(deleted_at__isnull=True).values_list("pk", flat=True))
    Task.objects.filter(pk__in=[task.pk, *kids]).update(
        deleted_at=now, delete_batch=uuid.uuid4(), updated_at=now, version=F("version") + 1
    )
    audit(user, "tasks.task.delete", "task", task.pk, subtasks_deleted=len(kids) or None)


@transaction.atomic
def delete_list(user, task_list):
    tl = _lock_list(task_list)
    require(user, "delete", "task_list", tl)
    now = timezone.now()
    batch = uuid.uuid4()
    n = Task.objects.filter(task_list=tl, deleted_at__isnull=True).update(deleted_at=now, delete_batch=batch)
    tl.deleted_at, tl.delete_batch = now, batch
    tl.save(update_fields=["deleted_at", "delete_batch"])
    audit(user, "tasks.list.delete", "task_list", tl.pk, tasks_deleted=n or None)


def _same_deletion(row):
    """Q for the rows removed by the same delete call as `row`: the batch id, exactly. A row deleted before batch ids existed has none, and old
    groups cannot be told apart from separate deletions at the same instant, so such a row is restored alone and never pulls others in."""
    if row.delete_batch is not None:
        return Q(delete_batch=row.delete_batch)
    return Q(pk=row.pk)


def _in_same_deletion(a, b):
    return a.delete_batch is not None and a.delete_batch == b.delete_batch


def trash_for(user):
    """The owner's deleted lists and deleted tasks in live lists, newest first. A task deleted together with its parent comes back with the
    parent, so only the head of each deletion is listed: a task whose parent is live or was deleted at another moment. Tasks inside a deleted
    list are not listed until that list is restored."""
    lists = list(TaskList.objects.filter(owner=user, deleted_at__isnull=False).order_by("-deleted_at"))
    tasks = [
        t for t in Task.objects.filter(task_list__owner=user, task_list__deleted_at__isnull=True, deleted_at__isnull=False)
        .select_related("task_list", "parent").order_by("-deleted_at", "id")
        if t.parent is None or t.parent.deleted_at is None or not _in_same_deletion(t.parent, t)
    ]
    return lists, tasks


@transaction.atomic
def restore_task(user, task):
    """Undo one task deletion: the task and the subtasks deleted in the same delete come back. Same owner right as delete. A subtask whose
    parent is still deleted is refused (restore the parent first). Versions move so an old form is stale."""
    tl = _lock_list(task.task_list)
    fresh = _fresh(task)
    _refuse_if_moved(fresh, tl)
    fresh.task_list = tl
    require(user, "restore", "task", fresh)
    if fresh.parent_id is not None:
        parent = Task.objects.get(pk=fresh.parent_id)
        if parent.deleted_at is not None:
            raise ValidationError("Restore its parent task first.")
        if parent.assignee_id:  # the same rule as adding a subtask: a task given to someone cannot hold subtasks
            raise ValidationError("A subtask must sit directly under a task of your own in the same list.")
    now = timezone.now()
    ids = [fresh.pk, *Task.objects.filter(_same_deletion(fresh), parent=fresh, deleted_at__isnull=False).values_list("pk", flat=True)]
    Task.objects.filter(pk__in=ids).update(deleted_at=None, delete_batch=None, updated_at=now, version=F("version") + 1)
    if fresh.parent_id is not None and fresh.status != Task.DONE:
        # the same build default as adding a subtask: a completed task never holds an open subtask
        Task.objects.filter(pk=fresh.parent_id, status=Task.DONE).update(
            status=Task.OPEN, completed_at=None, version=F("version") + 1, updated_at=now
        )
    audit(user, "tasks.task.restore", "task", fresh.pk, subtasks_restored=len(ids) - 1 or None)


@transaction.atomic
def restore_list(user, task_list):
    """Undo one list deletion: the list and the tasks deleted with it (the same moment) come back. Tasks deleted earlier stay in the trash."""
    tl = _lock_list(task_list)
    require(user, "restore", "task_list", tl)
    now = timezone.now()
    n = Task.objects.filter(_same_deletion(tl), task_list=tl, deleted_at__isnull=False).update(
        deleted_at=None, delete_batch=None, updated_at=now, version=F("version") + 1
    )
    tl.deleted_at = tl.delete_batch = None
    tl.save(update_fields=["deleted_at", "delete_batch"])
    audit(user, "tasks.list.restore", "task_list", tl.pk, tasks_restored=n or None)


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


DUE_SOON_DAYS = 7  # build default for how far ahead the Due soon page looks, not a stated policy


def due_soon(user, today):
    """Open tasks with a due date that this person already sees: tasks in their own lists, tasks
    assigned to them, and project tasks assigned to them (not in the Done column). Read only.
    Returns {"overdue": [...], "today": [...], "soon": [...]} of dicts, each sorted by date then title."""
    from datetime import timedelta

    from django.db.models import Q

    from apps.tasks import projects

    last = today + timedelta(days=DUE_SOON_DAYS)
    mine = (
        Task.objects.filter(
            Q(task_list__owner=user) | Q(assignee=user),
            deleted_at__isnull=True,
            task_list__deleted_at__isnull=True,
            status=Task.OPEN,
            due_date__isnull=False,
            due_date__lte=last,
        )
        .select_related("task_list__owner")
        .distinct()
    )
    rows = [
        {"title": t.title, "due": t.due_date, "where": t.task_list.name if t.task_list.owner_id == user.pk else "From " + t.task_list.owner.full_name, "kind": "list" if t.task_list.owner_id == user.pk else "assigned", "ref": t.task_list.public_id}
        for t in mine
    ]
    for t in projects.assigned_in_projects(user).filter(due_date__isnull=False, due_date__lte=last).exclude(column__name="Done"):
        rows.append({"title": t.title, "due": t.due_date, "where": t.project.name, "kind": "project", "ref": t.project.public_id})
    rows.sort(key=lambda r: (r["due"], r["title"].lower()))
    out = {"overdue": [], "today": [], "soon": []}
    for r in rows:
        out["overdue" if r["due"] < today else "today" if r["due"] == today else "soon"].append(r)
    return out
