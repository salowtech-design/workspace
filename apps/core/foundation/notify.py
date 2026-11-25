from typing import Any

from django.utils import timezone

from apps.core.models import Notification


def notify(user: Any, kind: str, title: str, link: str = "") -> Notification:
    return Notification.objects.create(user=user, kind=kind, title=title[:200], link=link[:300])


def unread_count(user: Any) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def mark_all_read(user: Any) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
