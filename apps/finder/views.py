from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.core.foundation.search import search
from apps.core.foundation.throttle import throttled

QUERY_MIN, QUERY_MAX = 2, 100
SEARCH_LIMIT = 30  # searches per person per minute
PER_PAGE = 20
GROUPS = {
    "task": "Tasks",
    "project_task": "Project tasks",
    "event": "Calendar",
    "person": "People",
    "folder": "Drive",
    "file": "Drive",
}
FILTERS = {"tasks": ("task", "project_task"), "calendar": ("event",), "people": ("person",)}
SEARCHER_FOR_FILTER = {
    "drive": ("drive",),
    "tasks": ("tasks",),
    "calendar": ("calendar",),
    "people": ("people",),
}


@login_required
def search_page(request):
    q = (request.GET.get("q") or "").strip()
    scope = request.GET.get("in", "")
    ctx = {"q": q, "scope": scope, "groups": None, "ran": False}
    if not q:
        return render(request, "finder/search.html", ctx)
    if len(q) < QUERY_MIN:
        messages.error(request, f"Type at least {QUERY_MIN} characters.")
        return render(request, "finder/search.html", ctx, status=400)
    if len(q) > QUERY_MAX:
        messages.error(request, f"Keep the search to {QUERY_MAX} characters or fewer.")
        return render(request, "finder/search.html", ctx, status=400)
    if throttled(request.user, "search", SEARCH_LIMIT):
        messages.error(request, "Too many searches in a minute. Wait and try again.")
        return render(request, "finder/search.html", ctx, status=429)
    types = SEARCHER_FOR_FILTER.get(scope)
    hits = search(request.user, q, types=types, limit=PER_PAGE)
    grouped = {}
    for h in hits:
        grouped.setdefault(GROUPS.get(h.resource_type, "Other"), []).append(
            {"title": h.title, "url": h.url}  # only title and url leave this view
        )
    ctx.update(groups=sorted(grouped.items()), ran=True)
    return render(request, "finder/search.html", ctx)
