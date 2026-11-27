"""Searchers for Tasks, Calendar and People. Each filters to what the person may see inside
its own query (so hidden rows never crowd out visible ones); the shared hook then runs can() on
every hit as a backstop. Matching is case-insensitive through Upper() because TiDB's default
collation is binary. Results are (type, object, title, url); the page shows only title and url."""

from django.db.models import Q
from django.db.models.functions import Upper

from apps.accounts.models import CustomUser
from apps.accounts.permissions import has_permission
from apps.core.foundation.permissions import register_policy
from apps.core.foundation.search import rank_text, relevance

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
        .annotate(_rk=relevance("_t", needle))
        .order_by("_rk", "-id")[:limit]
    )
    found = []
    for t in mine:
        if t.task_list.owner_id == user.pk:
            found.append(("task", t, t.title, f"/tasks/list/{t.task_list.public_id}/"))
        else:
            found.append(("task", t, t.title, "/tasks/assigned/"))
    proj = (
        ProjectTask.objects.annotate(_t=Upper("title"))
        .filter(_t__contains=needle, deleted_at__isnull=True, project__deleted_at__isnull=True)
        .filter(
            project__members__person=user,
            project__members__removed_at__isnull=True,
        )
        .select_related("project")
        .annotate(_rk=relevance("_t", needle))
        .distinct()
        .order_by("_rk", "-id")[:limit]
    )
    for t in proj:
        found.append(("project_task", t, t.title, f"/tasks/projects/{t.project.public_id}/"))
    from apps.tasks.models import Project

    named = (
        Project.objects.annotate(_n=Upper("name"))
        .filter(_n__contains=needle, deleted_at__isnull=True)
        .filter(members__person=user, members__removed_at__isnull=True)
        .annotate(_rk=relevance("_n", needle))
        .distinct()
        .order_by("_rk", "-id")[:limit]
    )
    for p in named:
        found.append(("project", p, p.name, f"/tasks/projects/{p.public_id}/"))
    found.sort(key=lambda h: rank_text(h[2], query))  # stable: equal rank keeps the order above
    yield from found


def search_events(user, query, limit):
    from apps.events.models import Event, Guest

    rows = (
        Event.objects.annotate(_t=Upper("title"))
        .filter(_t__contains=query.upper(), status=Event.CONFIRMED)
        .filter(Q(organizer=user) | Q(pk__in=Guest.objects.filter(person=user).values("event_id")))
        .annotate(_rk=relevance("_t", query.upper()))
        .order_by("_rk", "-start_utc")[:limit]
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
        .annotate(_rk=relevance("_n", needle, also_exact="_i"))
        .order_by("_rk", "employee_id")[:limit]
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


def _project_policy(user, action, p):
    from apps.tasks.projects import can_view

    return action == "view" and can_view(user, p)


def search_posts(user, query, limit):
    """Work log and feed post titles. Visibility is the posts module's own rule (list_posts_for_user), the
    same one the post page uses, applied inside the query."""
    from django.urls import reverse

    from apps.posts.selectors import list_posts_for_user

    needle = query.upper()
    qs = (
        list_posts_for_user(user)
        .annotate(_t=Upper("title"))
        .filter(_t__contains=needle)
        .annotate(_rk=relevance("_t", needle))
        .order_by("_rk", "-id")[:limit]
    )
    for p in qs:
        yield ("post", p, p.title, reverse("posts:detail", args=[p.pk]))


def _post_policy(user, action, p):
    from apps.posts.selectors import list_posts_for_user

    return action == "view" and list_posts_for_user(user).filter(pk=p.pk).exists()


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


CHAT_SNIPPET = 80


def search_chat(user, query, limit):
    """Chat message text, only in conversations the person is an active member of (the same rule the
    conversation page uses). The title is the conversation label and the start of the message."""
    from django.urls import reverse

    from apps.chat.models import Member, Message, Space

    from apps.chat.services import _live

    if _live(user.pk) is None:  # the chat page's own gate: active and not suspended/exited
        return
    needle = query.upper()
    mine = Member.objects.filter(person=user, left_at__isnull=True).values("space_id")
    rows = (
        Message.objects.annotate(_b=Upper("body"))
        .filter(_b__contains=needle, space_id__in=mine)
        .select_related("space")
        .annotate(_rk=relevance("_b", needle))
        .order_by("_rk", "-id")[:limit]
    )
    for m in rows:
        sp = m.space
        if sp.kind == Space.DM:
            other = (
                Member.objects.filter(space=sp).exclude(person=user).select_related("person").first()
            )
            label = other.person.full_name if other else "Direct message"
        else:
            label = sp.name
        text = " ".join(m.body.split())
        if len(text) > CHAT_SNIPPET:
            text = text[:CHAT_SNIPPET].rstrip() + "..."
        if m.thread_root_seq:
            url = reverse("chat:thread", args=[sp.public_id, m.thread_root_seq])
        else:
            url = reverse("chat:space", args=[sp.public_id])
        yield ("chat_message", m, f"{label}: {text}", url)


def _chat_policy(user, action, m):
    from apps.chat.models import Member
    from apps.chat.services import _live

    return (
        action == "view"
        and _live(user.pk) is not None
        and Member.objects.filter(space_id=m.space_id, person=user, left_at__isnull=True).exists()
    )


def register_all():
    from apps.core.foundation.search import register_searcher

    register_policy("project_task", _project_task_policy)
    register_policy("project", _project_policy)
    register_policy("event", _event_policy)
    register_policy("post", _post_policy)
    register_policy("chat_message", _chat_policy)
    register_policy(PERSON, _person_policy)
    register_searcher("tasks", search_tasks)
    register_searcher("calendar", search_events)
    register_searcher("people", search_people)
    register_searcher("posts", search_posts)
    register_searcher("chat", search_chat)
