from apps.core.foundation.notify import unread_count


def unread_notifications(request):
    """Unread count for the nav. One indexed count for a signed-in, active person; nothing else."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or getattr(user, "status", "active") != "active":
        return {"unread_notifications": 0}
    try:
        return {"unread_notifications": unread_count(user)}
    except Exception:  # noqa: BLE001 - a nav badge must never break a page
        return {"unread_notifications": 0}
