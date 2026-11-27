import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from apps.core.foundation.search import search_grouped
from apps.core.foundation.throttle import throttled

QUERY_MIN, QUERY_MAX = 2, 100
SEARCH_LIMIT = 30  # searches per person per minute
PER_MODULE = 20
MODULES = {"drive": "Drive", "tasks": "Tasks", "calendar": "Calendar", "people": "People", "posts": "Work Logs & Feed", "chat": "Chat"}
EMPLOYEE_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _link(hit):
    """Rebuild a person link from the ID after checking its format, never by string pasting."""
    if hit.resource_type == "person":
        eid = hit.url.rsplit("/", 2)[-2]
        if EMPLOYEE_ID.match(eid):
            return reverse("directory:profile", args=[eid])
        return reverse("directory:list")
    return hit.url


@login_required
def search_page(request):
    q = (request.GET.get("q") or "").strip()
    scope = request.GET.get("in", "")
    if request.GET.get("format") == "json":
        return _json_search(request, q, scope)
    ctx = {"q": q, "scope": scope, "groups": None, "ran": False}
    if scope and scope not in MODULES:
        messages.error(request, "Unknown place to search. Pick one from the list.")
        ctx["scope"] = ""
        return render(request, "finder/search.html", ctx, status=400)
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
    found = search_grouped(
        request.user, q, types=(scope,) if scope else None, per_module=PER_MODULE
    )
    groups = []
    for name, (hits, truncated) in found.items():
        if hits:  # only title and url leave this view
            groups.append(
                {
                    "name": MODULES.get(name, name),
                    "items": [{"title": h.title, "url": _link(h)} for h in hits],
                    "truncated": truncated,
                    "per": PER_MODULE,
                }
            )
    ctx.update(groups=groups, ran=True)
    return render(request, "finder/search.html", ctx)


def _json_search(request, q, scope):
    """The quick-search palette's data: the same checks, throttle and search as the page, and only group name,
    title and url come back. Errors are short fixed texts. Never cached."""

    def reply(body, status=200):
        r = JsonResponse(body, status=status)
        r["Cache-Control"] = "no-store"
        return r

    if scope and scope not in MODULES:
        return reply({"error": "Unknown place to search."}, 400)
    if len(q) < QUERY_MIN or len(q) > QUERY_MAX:
        return reply({"error": f"Type {QUERY_MIN} to {QUERY_MAX} characters."}, 400)
    if throttled(request.user, "search", SEARCH_LIMIT):
        return reply({"error": "Too many searches. Wait a moment."}, 429)
    found = search_grouped(request.user, q, types=(scope,) if scope else None, per_module=PER_MODULE)
    groups = [
        {"name": MODULES.get(name, name), "items": [{"title": h.title, "url": _link(h)} for h in hits], "more": truncated}
        for name, (hits, truncated) in found.items()
        if hits
    ]
    return reply({"groups": groups})
