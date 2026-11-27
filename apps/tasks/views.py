import datetime
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.functions import Upper
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.models import CustomUser
from apps.core.foundation.permissions import can
from apps.tasks import projects
from apps.tasks import services as svc
from apps.tasks.models import Task, TaskList


def _list_or_404(user, list_id, action="view"):
    tl = get_object_or_404(TaskList, public_id=list_id)
    if not can(user, action, "task_list", tl):
        raise Http404  # unknown and forbidden look the same
    return tl


def _run(request, fn, *args, ok="", **kwargs):
    try:
        out = fn(*args, **kwargs)
        if ok:
            messages.success(request, ok)
        return out
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except PermissionDenied:
        messages.error(request, "You do not have permission to do that.")
    return None


def _date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise ValidationError("Use a date like 2026-12-31.") from None


@login_required
def home(request):
    lists = list(svc.lists_for(request.user))
    if lists:
        return redirect("tasks:list", list_id=lists[0].public_id)
    return render(request, "tasks/list.html", {"lists": [], "tl": None, "tasks": [], "tab": "mine"})


@login_required
def list_view(request, list_id, rename_value=None):
    tl = _list_or_404(request.user, list_id)
    rows = list(svc.tasks_in(request.user, tl).select_related("assignee"))
    tops = [t for t in rows if t.parent_id is None]
    subs = {}
    for t in rows:
        if t.parent_id:
            subs.setdefault(t.parent_id, []).append(t)
    tree = [(t, subs.get(t.pk, [])) for t in tops]
    return render(
        request,
        "tasks/list.html",
        {"lists": svc.lists_for(request.user), "tl": tl, "tree": tree, "tab": "mine",
         "rename_value": tl.name if rename_value is None else rename_value,
         "today": timezone.localdate()},
    )


@login_required
def assigned_view(request):
    return render(
        request,
        "tasks/assigned.html",
        {
            "lists": svc.lists_for(request.user),
            "tasks": svc.assigned_to(request.user),
            "today": timezone.localdate(),
            "project_tasks": list(projects.assigned_in_projects(request.user)),
            "tab": "assigned",
        },
    )


@login_required
@require_POST
def new_list(request):
    tl = _run(
        request, svc.create_list, request.user, request.POST.get("name", ""), ok="List created."
    )
    return redirect("tasks:list", list_id=tl.public_id) if tl else redirect("tasks:home")


@login_required
@require_POST
def rename_list(request, list_id):
    tl = _list_or_404(request.user, list_id, "edit")
    entered = request.POST.get("new_name", "")
    if _run(request, svc.rename_list, request.user, tl, entered, ok="List renamed.") is None:
        return list_view(request, list_id, rename_value=entered)  # keep what was typed, with the error shown
    return redirect("tasks:list", list_id=tl.public_id)


@login_required
@require_POST
def add_task(request, list_id):
    tl = _list_or_404(request.user, list_id, "add")
    parent = None
    pid = request.POST.get("parent", "")
    if pid:
        try:
            pid = uuid.UUID(pid)
        except ValueError:
            raise Http404 from None
        parent = get_object_or_404(Task, public_id=pid, task_list=tl, deleted_at__isnull=True)
    try:
        due = _date(request.POST.get("due"))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("tasks:list", list_id=tl.public_id)
    _run(request, svc.create_task, request.user, tl, request.POST.get("title", ""),
         due_date=due, parent=parent, ok="Task added.")  # fmt: skip
    return redirect("tasks:list", list_id=tl.public_id)


@login_required
@require_http_methods(["GET", "POST"])
def delete_list(request, list_id):
    tl = _list_or_404(request.user, list_id, "delete")
    if request.method == "GET" or request.POST.get("confirm") != "yes":
        n = Task.objects.filter(task_list=tl, deleted_at__isnull=True).count()
        return render(request, "tasks/confirm.html", {"what": "list", "name": tl.name, "extra": n,
                      "post_url": request.path, "back": redirect("tasks:list", list_id=tl.public_id).url, "tab": "mine"})  # fmt: skip
    _run(request, svc.delete_list, request.user, tl, ok="List deleted.")
    return redirect("tasks:home")


def _back(request, task):
    if task.task_list.owner_id == request.user.pk:
        return redirect("tasks:list", list_id=task.task_list.public_id)
    return redirect("tasks:assigned")


@login_required
@require_POST
def move_task(request, task_id):
    task = svc.get_task(request.user, task_id, "edit")
    try:
        target_id = uuid.UUID(request.POST.get("to", ""))
    except ValueError:
        raise Http404 from None
    target = _list_or_404(request.user, target_id, "add")
    try:
        version = int(request.POST.get("version", ""))
    except ValueError:
        raise Http404 from None
    if _run(request, svc.move_task, request.user, task, version, target, ok="Task moved.") is None:
        return redirect("tasks:list", list_id=task.task_list.public_id)
    return redirect("tasks:list", list_id=target.public_id)


@login_required
@require_POST
def reorder_task(request, task_id):
    task = svc.get_task(request.user, task_id, "edit")
    try:
        version = int(request.POST.get("version", ""))
    except ValueError:
        raise Http404 from None
    _run(request, svc.reorder_task, request.user, task, version, request.POST.get("direction", ""))
    return redirect("tasks:list", list_id=task.task_list.public_id)


@login_required
@require_POST
def toggle(request, task_id):
    task = svc.get_task(request.user, task_id, "complete")
    _run(request, svc.set_status, request.user, task, task.status != Task.DONE)
    return _back(request, task)


@login_required
@require_http_methods(["GET", "POST"])
def edit(request, task_id):
    task = svc.get_task(request.user, task_id, "edit")
    if request.method == "GET":
        return render(request, "tasks/edit.html", {"task": task, "tab": "mine"})
    try:
        version = int(request.POST.get("version", ""))
        changes = {k: request.POST[k] for k in ("title", "notes") if k in request.POST}
        if "due" in request.POST:
            changes["due_date"] = _date(request.POST.get("due"))
    except (ValueError, ValidationError) as exc:
        messages.error(
            request, " ".join(getattr(exc, "messages", ["Check the fields and try again."]))
        )
        task.refresh_from_db()
        sent = {k: request.POST.get(k, "") for k in ("title", "notes", "due")}
        return render(
            request, "tasks/edit.html", {"task": task, "tab": "mine", "sent": sent}, status=400
        )
    sent = {k: request.POST.get(k, "") for k in ("title", "notes", "due")}
    try:
        svc.update_task(request.user, task, version, **changes)
    except svc.StaleVersion as exc:  # the only case that gets the conflict box and 409
        messages.error(request, " ".join(exc.messages))
        task.refresh_from_db()
        if task.deleted_at is not None:
            raise Http404 from None
        return render(
            request,
            "tasks/edit.html",
            {"task": task, "tab": "mine", "sent": sent, "conflict": True},
            status=409,
        )
    except ValidationError as exc:  # ordinary invalid input: keep typing, plain message, 400
        messages.error(request, " ".join(exc.messages))
        task.refresh_from_db()
        return render(
            request, "tasks/edit.html", {"task": task, "tab": "mine", "sent": sent}, status=400
        )
    except PermissionDenied:
        raise Http404 from None  # no longer allowed: same as not found
    messages.success(request, "Saved.")
    return _back(request, task)


ASSIGN_LIMIT = 30  # give actions per person per minute (own counter, same core limiter as Drive)
NEUTRAL = "That person cannot be given tasks. Check the employee ID."


@login_required
@require_POST
def assign_task(request, task_id):
    from apps.core.foundation.throttle import throttled

    task = svc.get_task(request.user, task_id, "assign")
    if throttled(request.user, "tasks-assign", ASSIGN_LIMIT):
        messages.error(request, "Too many changes in a minute. Wait and try again.")
        return _back(request, task)
    eid = (request.POST.get("employee_id") or "").strip()
    person = None
    if eid:
        person = (
            CustomUser.objects.annotate(u=Upper("employee_id"))
            .filter(u=eid.upper(), is_active=True, status="active")
            .first()
        )
        if person is None:
            messages.error(request, NEUTRAL)  # same words for unknown, inactive and refused
            return _back(request, task)
    try:
        svc.assign(request.user, task, person)
        messages.success(request, "Assignment saved.")
    except ValidationError as exc:
        text = " ".join(exc.messages)
        messages.error(request, NEUTRAL if "not active" in text else text)
    except PermissionDenied:
        messages.error(request, "You do not have permission to do that.")
    return _back(request, task)


@login_required
@require_http_methods(["GET", "POST"])
def delete_task(request, task_id):
    task = svc.get_task(request.user, task_id, "delete")
    kids = task.subtasks.filter(deleted_at__isnull=True).count()
    if request.method == "GET" or request.POST.get("confirm") != "yes":
        return render(request, "tasks/confirm.html", {"what": "task", "name": task.title, "extra": kids,
                      "post_url": request.path, "back": _back(request, task).url, "tab": "mine"})  # fmt: skip
    _run(request, svc.delete_task, request.user, task, ok="Task deleted.")
    return _back(request, task)


@login_required
def trash_view(request):
    lists, tasks = svc.trash_for(request.user)
    return render(request, "tasks/trash.html", {"lists": lists, "tasks": tasks, "tab": "trash"})


@login_required
@require_POST
def restore_task(request, task_id):
    t = Task.objects.filter(public_id=task_id).select_related("task_list").first()
    if t is None or not can(request.user, "restore", "task", t):
        raise Http404
    _run(request, svc.restore_task, request.user, t, ok="Task restored.")
    return redirect("tasks:trash")


@login_required
@require_POST
def restore_list(request, list_id):
    tl = TaskList.objects.filter(public_id=list_id).first()
    if tl is None or not can(request.user, "restore", "task_list", tl):
        raise Http404
    _run(request, svc.restore_list, request.user, tl, ok="List restored.")
    return redirect("tasks:trash")


@login_required
def due_soon_view(request):
    from django.utils import timezone

    # timezone.localdate() is the app's own date (TIME_ZONE = Asia/Kolkata), the same one the project board's Overdue filter uses.
    groups = svc.due_soon(request.user, timezone.localdate())
    return render(
        request,
        "tasks/due_soon.html",
        {"groups": groups, "days": svc.DUE_SOON_DAYS, "tab": "due", "lists": svc.lists_for(request.user)},
    )
