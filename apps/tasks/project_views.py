from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.foundation.throttle import throttled
from apps.tasks import projects as pj

PROJECT_LIMIT = 10  # new projects per person per minute


@login_required
def projects_home(request):
    return render(
        request,
        "tasks/projects.html",
        {"projects": pj.projects_for(request.user), "tab": "projects"},
    )


@login_required
@require_POST
def new_project(request):
    if throttled(request.user, "tasks-project", PROJECT_LIMIT):
        messages.error(request, "Too many changes in a minute. Wait and try again.")
        return redirect("tasks:projects")
    try:
        p = pj.create_project(request.user, request.POST.get("name", ""))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("tasks:projects")
    messages.success(request, "Project created. Only you can see it for now.")
    return redirect("tasks:project", project_id=p.public_id)


@login_required
def project_view(request, project_id):
    p = pj.get_project(request.user, project_id)
    by = pj.tasks_by_column(p)
    return render(
        request,
        "tasks/project.html",
        {
            "project": p,
            "columns": [(c, by.get(c.pk, [])) for c in p.columns.all()],
            "is_lead": _is_lead(request.user, p),
            "members": pj.members_of(p),
            "milestones": pj.milestones_of(p),
            "tab": "projects",
        },
    )


def _is_lead(user, project):
    m = pj._membership(user, project)
    return m is not None and m.role == "lead"


def _guarded(request, project, fn, *args, ok=""):
    try:
        out = fn(request.user, *args)
        if ok:
            messages.success(request, ok)
        return out
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except PermissionDenied:
        messages.error(request, "Only a project lead can change project tasks.")
    return None


@login_required
@require_POST
def add_project_task(request, project_id):
    p = pj.get_project(request.user, project_id)
    due = (request.POST.get("due") or "").strip() or None
    if due:
        import datetime

        try:
            due = datetime.date.fromisoformat(due)
        except ValueError:
            messages.error(request, "Use a date like 2026-12-31.")
            return redirect("tasks:project", project_id=p.public_id)
    try:
        col = int(request.POST.get("column", ""))
    except ValueError:
        raise Http404 from None
    _guarded(request, p, pj.add_task, p, col, request.POST.get("title", ""), "", due)
    return redirect("tasks:project", project_id=p.public_id)


@login_required
@require_POST
def move_project_task(request, task_id):
    t = pj.get_task(request.user, task_id)
    try:
        col, ver = int(request.POST.get("column", "")), int(request.POST.get("version", ""))
    except ValueError:
        raise Http404 from None
    _guarded(request, t.project, pj.move_task, t, col, ver)
    return redirect("tasks:project", project_id=t.project.public_id)


@login_required
@require_http_methods(["GET", "POST"])
def edit_project_task(request, task_id):
    t = pj.get_task(request.user, task_id)
    if not _is_lead(request.user, t.project):
        raise Http404  # members see tasks but only leads reach the edit form
    ctx = {"task": t, "tab": "projects", "project_task": True}
    if request.method == "GET":
        return render(request, "tasks/edit.html", ctx)
    sent = {k: request.POST.get(k, "") for k in ("title", "notes", "due")}
    try:
        import datetime

        version = int(request.POST.get("version", ""))
        changes = {k: request.POST[k] for k in ("title", "notes") if k in request.POST}
        if "due" in request.POST:
            raw = request.POST["due"].strip()
            try:
                changes["due_date"] = datetime.date.fromisoformat(raw) if raw else None
            except ValueError:
                raise ValidationError("Use a date like 2026-12-31.") from None
        pj.edit_task(request.user, t, version, **changes)
    except pj.StaleVersion as exc:
        messages.error(request, " ".join(exc.messages))
        t.refresh_from_db()
        if t.deleted_at is not None:
            raise Http404 from None
        return render(
            request,
            "tasks/edit.html",
            {**ctx, "task": t, "sent": sent, "conflict": True},
            status=409,
        )
    except (ValueError, ValidationError) as exc:
        messages.error(
            request, " ".join(getattr(exc, "messages", ["Check the fields and try again."]))
        )
        t.refresh_from_db()
        return render(request, "tasks/edit.html", {**ctx, "task": t, "sent": sent}, status=400)
    except PermissionDenied:
        raise Http404 from None
    messages.success(request, "Saved.")
    return redirect("tasks:project", project_id=t.project.public_id)


@login_required
@require_http_methods(["GET", "POST"])
def delete_project_task(request, task_id):
    t = pj.get_task(request.user, task_id)
    if not _is_lead(request.user, t.project):
        raise Http404
    back = reverse("tasks:project", args=[t.project.public_id])
    if request.method == "GET" or request.POST.get("confirm") != "yes":
        return render(
            request,
            "tasks/confirm.html",
            {"what": "task", "name": t.title, "extra": 0, "post_url": request.path,
             "back": back, "tab": "projects"},
        )  # fmt: skip
    try:
        pj.delete_task(request.user, t)
    except (PermissionDenied, Http404):
        raise Http404 from None
    messages.success(request, "Task deleted.")
    return redirect(back)


@login_required
@require_POST
def add_milestone(request, project_id):
    import datetime

    p = pj.get_project(request.user, project_id)
    raw = (request.POST.get("due") or "").strip()
    try:
        due = datetime.date.fromisoformat(raw) if raw else None
    except ValueError:
        messages.error(request, "Use a date like 2026-12-31.")
        return redirect("tasks:project", project_id=p.public_id)
    _guarded(
        request, p, pj.create_milestone, p, request.POST.get("name", ""), due, ok="Milestone added."
    )
    return redirect("tasks:project", project_id=p.public_id)


@login_required
@require_POST
def task_milestone(request, task_id):
    t = pj.get_task(request.user, task_id)
    raw = request.POST.get("milestone", "")
    try:
        mid = int(raw) if raw else ""
    except ValueError:
        raise Http404 from None
    _guarded(request, t.project, pj.set_milestone, t, mid)
    return redirect("tasks:project", project_id=t.project.public_id)
