import uuid

from django.conf import settings
from django.db import models

TITLE_MAX = 200
LOCATION_MAX = 200
DESCRIPTION_MAX = 2000


class Event(models.Model):
    """An event, single or repeating. Times are stored in UTC with the organiser's time zone name. A repeat rule,
    guests with answers, all-day events (single only), and a plain-text location and description are built. Rooms and resources, reminders and
    email are NOT built. Location and description are free text, not a room booking."""

    CONFIRMED, CANCELLED = "confirmed", "cancelled"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="organised_events"
    )
    title = models.CharField(max_length=TITLE_MAX)
    start_utc = models.DateTimeField()
    end_utc = models.DateTimeField()
    tz = models.CharField(max_length=40)
    location = models.CharField(max_length=LOCATION_MAX, blank=True, default="")  # free text, not a room
    description = models.TextField(blank=True, default="")  # plain text, DESCRIPTION_MAX enforced in services
    all_day = models.BooleanField(default=False)  # whole local days: start is a local midnight, end is the midnight after the last day. Never repeats.
    status = models.CharField(max_length=10, default=CONFIRMED)
    version = models.PositiveIntegerField(default=1)
    # Repeat rule (all empty for a single event). start_utc/end_utc are the first occurrence.
    freq = models.CharField(max_length=7, blank=True, default="")  # daily, weekly, monthly
    interval = models.PositiveSmallIntegerField(default=1)
    weekdays = models.CharField(max_length=13, blank=True, default="")  # "0,2,4", Monday is 0
    count = models.PositiveSmallIntegerField(null=True, blank=True)
    until = models.DateField(null=True, blank=True)  # last allowed local date
    last_end_utc = models.DateTimeField(null=True, blank=True)  # end of the last occurrence
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

    # Plain words for the pages. Only display: the stored value is unchanged.
    LABELS = {
        "needs_action": "Not answered yet",
        "accepted": "Accepted",
        "declined": "Declined",
        "tentative": "Maybe",
    }

    @property
    def response_label(self):
        return self.LABELS.get(self.response, self.response)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "person"], name="event_guest_uniq")]
        indexes = [models.Index(fields=["person"])]


class EventException(models.Model):
    """One occurrence of a repeating event that the organiser cancelled (local date in the
    event's own zone)."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="exceptions")
    local_date = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "local_date"], name="event_exception_uniq")
        ]
