import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

from apps.core.foundation.search import search_grouped
from apps.core.foundation.throttle import throttled

QUERY_MIN, QUERY_MAX = 2, 100
SEARCH_LIMIT = 30  # searches per person per minute
PER_MODULE = 20
MODULES = {"drive": "Drive", "tasks": "Tasks", "calendar": "Calendar", "people": "People"}
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
