import logging

from apps.core.foundation.notify import unread_count

log = logging.getLogger(__name__)


CHAT_BADGE_CAP = 99


def unread_chat(request):
    """Unread chat messages for the nav, the same rule as the Chat list: after the member's read mark, not the
    person's own, in spaces they are a current member of. One query, read only up to the cap; nothing else."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or getattr(user, "status", "active") != "active":
        return {"unread_chat": 0}
    try:
        from django.db.models import F

        from apps.chat.models import Message

        rows = (
            Message.objects.filter(
                space__members__person=user,
                space__members__left_at__isnull=True,
                seq__gt=F("space__members__last_read_seq"),
            )
            .exclude(author=user)
            .values_list("pk", flat=True)[: CHAT_BADGE_CAP + 1]
        )
        n = len(list(rows))
        return {"unread_chat": n, "unread_chat_label": f"{CHAT_BADGE_CAP}+" if n > CHAT_BADGE_CAP else str(n)}
    except Exception:  # noqa: BLE001 - a nav badge must never break a page, but it is logged
        log.exception("unread chat count failed")
        return {"unread_chat": 0}


def unread_notifications(request):
    """Unread count for the nav. One indexed count for a signed-in, active person; nothing else."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or getattr(user, "status", "active") != "active":
        return {"unread_notifications": 0}
    try:
        return {"unread_notifications": unread_count(user)}
    except Exception:  # noqa: BLE001 - a nav badge must never break a page, but it is logged
        log.exception("unread notification count failed")
        return {"unread_notifications": 0}
