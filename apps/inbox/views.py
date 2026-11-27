"""A person's own in-app notifications. Nothing here is emailed or pushed."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core.foundation.notify import mark_all_read
from apps.core.models import Notification

SHOWN = 50


def _safe(link):
    bad = any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F or c.isspace() for c in link)
    return (
        link
        if link.startswith("/") and not link.startswith("//") and "\\" not in link and not bad
        else ""
    )


@login_required
@never_cache
def home(request):
    if getattr(request.user, "status", "active") != "active":
        raise Http404
    rows = Notification.objects.filter(user=request.user).order_by("-created_at", "-id")[:SHOWN]
    items = [
        {"id": n.pk, "title": n.title, "link": _safe(n.link), "when": n.created_at, "unread": n.read_at is None}
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


@login_required
@require_POST
def open_notice(request, notice_id: int):
    """Open one of my own notices (POST only, so a prefetch or a cross-site link cannot clear it): mark just that one read, then go to its stored link. Someone else's
    notice, a missing one and an inactive account all get the same 404. A notice with no safe link
    goes back to the inbox. The destination page does its own access check."""
    if getattr(request.user, "status", "active") != "active":
        raise Http404
    n = Notification.objects.filter(pk=notice_id, user=request.user).first()
    if n is None:
        raise Http404
    if n.read_at is None:
        Notification.objects.filter(pk=n.pk, user=request.user, read_at__isnull=True).update(
            read_at=timezone.now()
        )
    return redirect(_safe(n.link) or "inbox:home")
