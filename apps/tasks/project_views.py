from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

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
