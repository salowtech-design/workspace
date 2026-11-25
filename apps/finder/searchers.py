"""Searchers for Tasks, Calendar and People. Each filters to what the person may see inside
its own query (so hidden rows never crowd out visible ones); the shared hook then runs can() on
every hit as a backstop. Matching is case-insensitive through Upper() because TiDB's default
collation is binary. Results are (type, object, title, url); the page shows only title and url."""

from django.db.models import Q
from django.db.models.functions import Upper

from apps.accounts.models import CustomUser
from apps.accounts.permissions import has_permission
from apps.core.foundation.permissions import register_policy

PERSON = "person"


def _like(field, query):
    return Upper(field), query.upper()


def search_tasks(user, query, limit):
    from apps.tasks.models import ProjectTask, Task

    needle = query.upper()
    mine = (
        Task.objects.annotate(_t=Upper("title"))
        .filter(_t__contains=needle, deleted_at__isnull=True, task_list__deleted_at__isnull=True)
        .filter(Q(task_list__owner=user) | Q(assignee=user))
        .select_related("task_list")
        .order_by("-id")[:limit]
    )
    for t in mine:
        if t.task_list.owner_id == user.pk:
            yield ("task", t, t.title, f"/tasks/list/{t.task_list.public_id}/")
        else:
            yield ("task", t, t.title, "/tasks/assigned/")
    proj = (
        ProjectTask.objects.annotate(_t=Upper("title"))
        .filter(_t__contains=needle, deleted_at__isnull=True, project__deleted_at__isnull=True)
        .filter(
            project__members__person=user,
            project__members__removed_at__isnull=True,
        )
        .select_related("project")
        .distinct()
        .order_by("-id")[:limit]
    )
    for t in proj:
        yield ("project_task", t, t.title, f"/tasks/projects/{t.project.public_id}/")


def search_events(user, query, limit):
    from apps.events.models import Event, Guest

    rows = (
        Event.objects.annotate(_t=Upper("title"))
        .filter(_t__contains=query.upper(), status=Event.CONFIRMED)
        .filter(Q(organizer=user) | Q(pk__in=Guest.objects.filter(person=user).values("event_id")))
        .order_by("-start_utc")[:limit]
    )
    for e in rows:
        yield ("event", e, e.title, f"/calendar/event/{e.public_id}/")


def search_people(user, query, limit):
    """Name and employee ID only (never phone, email or emergency contact). Build default:
    anyone who can open the directory can find any active or onboarding person."""
    if not has_permission(user, "accounts.user.view"):
        return
    needle = query.upper()
    rows = (
        CustomUser.objects.annotate(_n=Upper("full_name"), _i=Upper("employee_id"))
        .filter(Q(_n__contains=needle) | Q(_i__contains=needle))
        .filter(status__in=[CustomUser.Status.ACTIVE, CustomUser.Status.ONBOARDING])
        .order_by("employee_id")[:limit]
    )
    for p in rows:
        yield (
            PERSON,
            p,
            f"{p.full_name} ({p.employee_id})",
            f"/directory/profile/{p.employee_id}/",
        )


def _project_task_policy(user, action, t):
    from apps.tasks.projects import can_view

    return action == "view" and t.deleted_at is None and can_view(user, t.project)


def _event_policy(user, action, e):
    from apps.events.models import Event, Guest

    if action != "view" or e.status == Event.CANCELLED:
        return False
    return e.organizer_id == user.pk or Guest.objects.filter(event=e, person=user).exists()


def _person_policy(user, action, p):
    return (
        action == "view"
        and has_permission(user, "accounts.user.view")
        and p.status in (CustomUser.Status.ACTIVE, CustomUser.Status.ONBOARDING)
    )


def register_all():
    from apps.core.foundation.search import register_searcher

    register_policy("project_task", _project_task_policy)
    register_policy("event", _event_policy)
    register_policy(PERSON, _person_policy)
    register_searcher("tasks", search_tasks)
    register_searcher("calendar", search_events)
    register_searcher("people", search_people)
