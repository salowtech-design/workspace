from typing import Optional

from django.db.models import QuerySet

from apps.attendance.models import WorkSession


def get_open_session(user) -> Optional[WorkSession]:
    """Returns the current open work session for the given user, if any."""
    return WorkSession.objects.filter(user=user, state=WorkSession.State.OPEN).first()


def get_user_sessions(user, limit: int = 10) -> QuerySet[WorkSession]:
    """Returns the most recent work sessions for the user."""
    return WorkSession.objects.filter(user=user).order_by("-check_in_at")[:limit]
