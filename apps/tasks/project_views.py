import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.foundation.throttle import throttled
from apps.tasks import projects as pj
from apps.tasks.models import ProjectTask

PROJECT_LIMIT = 10  # new projects per person per minute
COMMENT_LIMIT = 30  # comments per person per minute


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


def _board_filters(request, p, milestones):
    q = (request.GET.get("q") or "").strip()[:100]
    ms = request.GET.get("milestone", "")
    valid = {str(m.pk) for m in milestones}
    milestone = "none" if ms == "none" else (int(ms) if ms in valid else None)
    overdue = request.GET.get("overdue") == "1"
    return q, milestone, overdue


@login_required
def project_view(request, project_id):
    import datetime  # noqa: F401

    from django.utils import timezone

    p = pj.get_project(request.user, project_id)
    milestones = list(pj.milestones_of(p))
    q, milestone, overdue = _board_filters(request, p, milestones)
    filtered = bool(q or milestone is not None or overdue)
    by = pj.tasks_by_column(
        p, q, milestone, timezone.localdate() if overdue else None
    )
    shown = sum(len(v) for v in by.values())
    return render(
        request,
        "tasks/project.html",
        {
            "project": p,
            "columns": [(c, by.get(c.pk, [])) for c in p.columns.all()],
            "is_lead": _is_lead(request.user, p),
            "members": pj.members_of(p),
            "milestones": milestones,
            "filtered": filtered,
            "shown": shown,
            "total": ProjectTask.objects.filter(project=p, deleted_at__isnull=True).count(),
            "f_q": q,
            "f_milestone": "none" if milestone == "none" else (milestone or ""),
            "f_overdue": overdue,
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
def rename_project(request, project_id):
    p = pj.get_project(request.user, project_id)
    try:
        pj.rename_project(request.user, p, request.POST.get("new_name", ""))
        messages.success(request, "Project renamed.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except PermissionDenied:
        messages.error(request, "Only a project lead can rename the project.")
    return redirect("tasks:project", project_id=p.public_id)


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
             "back": back, "tab": "projects", "project_trash": reverse("tasks:project_trash", args=[t.project.public_id])},
        )  # fmt: skip
    try:
        pj.delete_task(request.user, t)
    except (PermissionDenied, Http404):
        raise Http404 from None
    messages.success(request, "Task deleted.")
    return redirect(back)


@login_required
@require_http_methods(["GET"])
def project_trash(request, project_id):
    p = pj.get_project(request.user, project_id)
    try:
        items = pj.deleted_tasks(request.user, p)
    except PermissionDenied:
        raise Http404 from None
    return render(request, "tasks/project_trash.html", {"project": p, "items": items, "tab": "projects"})


@login_required
@require_POST
def restore_project_task(request, task_id):
    t = ProjectTask.objects.filter(public_id=task_id).select_related("project").first()
    if t is None or not pj.can_view(request.user, t.project):
        raise Http404
    back = reverse("tasks:project_trash", args=[t.project.public_id])
    try:
        pj.restore_task(request.user, t)
    except (PermissionDenied, Http404):
        raise Http404 from None
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect(back)
    messages.success(request, "Task restored.")
    return redirect(reverse("tasks:project", args=[t.project.public_id]))


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
def edit_milestone(request, project_id, milestone_id):
    import datetime

    p = pj.get_project(request.user, project_id)
    raw = (request.POST.get("due") or "").strip()
    try:
        due = datetime.date.fromisoformat(raw) if raw else None
    except ValueError:
        messages.error(request, "Use a date like 2026-12-31.")
        return redirect("tasks:project", project_id=p.public_id)
    try:
        pj.edit_milestone(request.user, p, milestone_id, request.POST.get("name", ""), due)
        messages.success(request, "Milestone saved.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except PermissionDenied:
        messages.error(request, "Only a project lead can change milestones.")
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


def _comment_page(request, t, status=200, sent=""):
    raw = request.GET.get("before", "")
    before = int(raw) if re.fullmatch(r"[0-9]{1,18}", raw) else None  # ASCII digits only
    rows, older = pj.comments_of(t, before)
    return render(
        request,
        "tasks/comments.html",
        {"task": t, "project": t.project, "comments": rows, "older": older,
         "paged": before is not None, "sent": sent, "tab": "projects"},
        status=status,
    )  # fmt: skip


@login_required
@require_http_methods(["GET", "POST"])
def task_comments(request, task_id):
    t = pj.get_task(request.user, task_id)
    if request.method == "GET":
        return _comment_page(request, t)
    back = reverse("tasks:project_comments", args=[t.public_id])
    sent = request.POST.get("body", "")[:4000]
    if throttled(request.user, "tasks-comment", COMMENT_LIMIT):
        messages.error(request, "Too many comments in a minute. Wait and try again. Your text is kept below.")
        return _comment_page(request, t, status=429, sent=sent)
    try:
        pj.add_comment(request.user, t, request.POST.get("body", ""))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return _comment_page(request, t, status=400, sent=sent)
    messages.success(request, "Comment posted.")
    return redirect(back)


@login_required
@require_POST
def assign_project_task(request, task_id):
    """A lead sets or clears the assignee. Only current members can be picked (the service
    checks); nobody is notified."""
    t = pj.get_task(request.user, task_id)
    try:
        sver, raw = request.POST.get("version", ""), request.POST.get("person", "")
        if not re.fullmatch(r"[0-9]{1,18}", sver) or (raw and not re.fullmatch(r"[0-9]{1,18}", raw)):
            raise ValueError
        ver, pid = int(sver), (int(raw) if raw else None)
    except ValueError:
        raise Http404 from None
    person = None
    if pid is not None:
        from apps.accounts.models import CustomUser

        person = CustomUser.objects.filter(pk=pid).first()
        if person is None:
            messages.error(request, "That person is not a member of this project.")
            return redirect("tasks:project", project_id=t.project.public_id)
    _guarded(request, t.project, pj.assign_task, t, person, ver,
             ok="Assignee saved.")  # fmt: skip
    return redirect("tasks:project", project_id=t.project.public_id)
