from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
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
    return render(
        request,
        "tasks/project.html",
        {
            "project": p,
            "columns": p.columns.all(),
            "members": pj.members_of(p),
            "tab": "projects",
        },
    )
