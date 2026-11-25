"""A person's own in-app notifications. Nothing here is emailed or pushed."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core.foundation.notify import mark_all_read
from apps.core.models import Notification

SHOWN = 50


def _safe(link):
    return link if link.startswith("/") and not link.startswith("//") and "\\" not in link else ""


@login_required
@never_cache
def home(request):
    if getattr(request.user, "status", "active") != "active":
        raise Http404
    rows = Notification.objects.filter(user=request.user).order_by("-created_at", "-id")[:SHOWN]
    items = [
        {"title": n.title, "link": _safe(n.link), "when": n.created_at, "unread": n.read_at is None}
        for n in rows
    ]
    return render(request, "inbox/home.html", {"items": items, "shown": SHOWN})


@login_required
@require_POST
def read_all(request):
    if getattr(request.user, "status", "active") != "active":
        raise Http404
    n = mark_all_read(request.user)
    messages.success(request, f"Marked {n} as read." if n else "Nothing was unread.")
    return redirect("inbox:home")
