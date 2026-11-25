import uuid

from django.conf import settings
from django.db import models

TITLE_MAX = 200


class Event(models.Model):
    """A single (non-repeating) event. Times are stored in UTC with the organiser's time zone
    name. Recurrence, rooms, notifications and email are NOT built."""

    CONFIRMED, CANCELLED = "confirmed", "cancelled"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="organised_events"
    )
    title = models.CharField(max_length=TITLE_MAX)
    start_utc = models.DateTimeField()
    end_utc = models.DateTimeField()
    tz = models.CharField(max_length=40)
    status = models.CharField(max_length=10, default=CONFIRMED)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["start_utc", "end_utc"])]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_utc__gt=models.F("start_utc")), name="event_end_after_start"
            )
        ]  # enforced on SQLite; TiDB with tidb_enable_check_constraint OFF ignores it


class Guest(models.Model):
    NEEDS_ACTION, ACCEPTED, DECLINED, TENTATIVE = (
        "needs_action",
        "accepted",
        "declined",
        "tentative",
    )
    RESPONSES = (NEEDS_ACTION, ACCEPTED, DECLINED, TENTATIVE)

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="guests")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="event_invitations"
    )
    response = models.CharField(max_length=12, default=NEEDS_ACTION)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "person"], name="event_guest_uniq")]
        indexes = [models.Index(fields=["person"])]
