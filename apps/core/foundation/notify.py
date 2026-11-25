from typing import Any, Optional

from django.utils import timezone

from apps.core.foundation.permissions import can
from apps.core.models import Notification


def _safe_link(link: str) -> str:
    """Internal paths only: one leading slash, no scheme, no protocol-relative URL."""
    if not link:
        return ""
    if (
        not link.startswith("/")
        or link.startswith("//")
        or "\\" in link
        or any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or ch.isspace() for ch in link)
        or len(link) > 300
    ):
        raise ValueError("notification link must be an internal path starting with a single /")
    return link


def notify(
    user: Any,
    kind: str,
    title: str,
    link: str = "",
    *,
    check: Optional[tuple[str, Any]] = None,
) -> Notification:
    """Create a notification for `user`.

    Titles are rendered escaped by templates and must never carry message or file content.
    The link must be an internal path. Pass check=(resource_type, obj) when the link points at
    a protected object: the recipient must then be allowed to view it, otherwise
    PermissionError is raised and nothing is created.
    """
    link = _safe_link(link)
    if check is not None and not can(user, "view", check[0], check[1]):
        raise PermissionError("recipient may not view the notification target")
    return Notification.objects.create(user=user, kind=kind, title=title[:200], link=link)


def unread_count(user: Any) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def mark_all_read(user: Any) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
