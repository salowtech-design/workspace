"""Calendar step 1: single events, guests, responses, own view, free/busy.

Build defaults, not user policy (each labelled): any signed-in person may organise an event and
invite any active person; guests are seen by the organiser and by other guests; free/busy (no
titles) is readable for any active person. Nothing notifies anyone and no email is sent: an
invitation only appears on the guest's own calendar. Locking: the event row first, then re-read."""

import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404

from apps.core.foundation.audit import audit
from apps.events.models import TITLE_MAX, Event, Guest

MAX_GUESTS = 50  # build default
MAX_DURATION = datetime.timedelta(days=14)  # build default
MAX_RANGE = datetime.timedelta(days=62)  # longest window one read may ask for
MAX_PEOPLE = 50  # most people one free/busy read may ask about


class StaleEvent(ValidationError):
    """The event changed since the form was loaded."""


def _tz(name):
    try:
        return ZoneInfo((name or "").strip())
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValidationError("Unknown time zone.") from None


def _utc(local_start, local_end, tz_name):
    """Naive local datetimes in tz_name to aware UTC; refuses a missing or non-existent time."""
    tz = _tz(tz_name)
    if local_start.tzinfo or local_end.tzinfo:
        raise ValidationError("Give local times without an offset, plus a time zone.")
    out = []
    for t in (local_start, local_end):
        aware = t.replace(tzinfo=tz)
        back = aware.astimezone(datetime.timezone.utc).astimezone(tz).replace(tzinfo=None)
        if back != t:
            raise ValidationError("That local time does not exist in the time zone.")
        out.append(aware.astimezone(datetime.timezone.utc))
    if out[1] <= out[0]:
        raise ValidationError("The end must be after the start.")
    if out[1] - out[0] > MAX_DURATION:
        raise ValidationError("An event can last at most 14 days.")
    return out


def _title(title):
    title = (title or "").strip()
    if not title or len(title) > TITLE_MAX:
        raise ValidationError(f"A title of 1 to {TITLE_MAX} characters is needed.")
    return title


def _live_people(ids, exclude=None):
    User = get_user_model()
    ids = {int(i) for i in ids}
    ids.discard(exclude)
    found = list(User.objects.filter(pk__in=ids, is_active=True, status="active"))
    if len(found) != len(ids):
        raise ValidationError("Every guest must be an active person.")
    return found


def create_event(actor, title, local_start, local_end, tz_name, guest_ids=()):
    title = _title(title)
    start, end = _utc(local_start, local_end, tz_name)
    guest_ids = list(dict.fromkeys(guest_ids))
    if len(guest_ids) > MAX_GUESTS:
        raise ValidationError(f"At most {MAX_GUESTS} guests.")
    with transaction.atomic():
        me = (
            get_user_model()
            .objects.select_for_update()
            .filter(pk=actor.pk, is_active=True, status="active")
            .first()
        )
        if me is None:
            raise PermissionDenied("Not allowed.")
        guests = _live_people(guest_ids, exclude=actor.pk)
        ev = Event.objects.create(
            organizer=me, title=title, start_utc=start, end_utc=end, tz=tz_name.strip()
        )
        Guest.objects.bulk_create([Guest(event=ev, person=g) for g in guests])
        audit(me, "events.create", "event", ev.pk, guests=len(guests))
    return ev


def _locked(event_public_id, actor):
    """Lock the event row first, re-read the actor, return the live event or raise 404."""
    ev = Event.objects.select_for_update().filter(public_id=event_public_id).first()
    me = get_user_model().objects.filter(pk=actor.pk, is_active=True, status="active").first()
    if ev is None or me is None:
        raise Http404
    return ev, me


def _visible(ev, me):
    return ev.organizer_id == me.pk or Guest.objects.filter(event=ev, person=me).exists()


def update_event(actor, public_id, expected_version, title, local_start, local_end, tz_name):
    """Organiser only. A guest or stranger gets 404 (existence is not revealed)."""
    title = _title(title)
    start, end = _utc(local_start, local_end, tz_name)
    with transaction.atomic():
        ev, me = _locked(public_id, actor)
        if ev.organizer_id != me.pk or not _visible(ev, me):
            raise Http404
        if ev.status == Event.CANCELLED:
            raise ValidationError("A cancelled event cannot be changed.")
        same = (ev.title, ev.start_utc, ev.end_utc, ev.tz) == (title, start, end, tz_name.strip())
        if same:
            return ev  # nothing changes: no version bump, no audit row
        if ev.version != int(expected_version):
            raise StaleEvent("The event changed since you opened it.")
        ev.title, ev.start_utc, ev.end_utc, ev.tz = title, start, end, tz_name.strip()
        ev.version += 1
        ev.save()
        # Guest responses are NOT reset on a time change: that is a policy choice, not made.
        audit(me, "events.update", "event", ev.pk)
    return ev


def cancel_event(actor, public_id):
    with transaction.atomic():
        ev, me = _locked(public_id, actor)
        if ev.organizer_id != me.pk:
            raise Http404
        if ev.status == Event.CANCELLED:
            return ev
        ev.status = Event.CANCELLED
        ev.version += 1
        ev.save()
        audit(me, "events.cancel", "event", ev.pk)
    return ev


def respond(actor, public_id, response):
    if response not in (Guest.ACCEPTED, Guest.DECLINED, Guest.TENTATIVE):
        raise ValidationError("Unknown response.")
    with transaction.atomic():
        ev, me = _locked(public_id, actor)
        g = Guest.objects.select_for_update().filter(event=ev, person=me).first()
        if g is None:
            raise Http404
        if ev.status == Event.CANCELLED:
            raise ValidationError("This event was cancelled.")
        if g.response == response:
            return g
        g.response = response
        g.save(update_fields=["response"])
        audit(me, "events.respond", "event", ev.pk, response=response)
    return g


def _window(start, end):
    if start.tzinfo is None or end.tzinfo is None:
        raise ValidationError("Give an aware time window.")
    if end <= start or end - start > MAX_RANGE:
        raise ValidationError("The window must be positive and at most 62 days.")


def _declined(person_pk):
    """Events this person declined (a subquery, so the match is on one guest row)."""
    return Guest.objects.filter(person_id=person_pk, response=Guest.DECLINED).values("event_id")


def events_for(user, start, end):
    """Events on the user's own calendar (organised or invited), not cancelled, overlapping."""
    _window(start, end)
    return (
        Event.objects.filter(status=Event.CONFIRMED, start_utc__lt=end, end_utc__gt=start)
        .filter(Q(organizer=user) | Q(pk__in=Guest.objects.filter(person=user).values("event_id")))
        .exclude(pk__in=_declined(user.pk))
        .order_by("start_utc", "id")
    )


def free_busy(viewer, person_ids, start, end):
    """Busy intervals only (no titles, no guests), per person. A declined or cancelled event is
    not busy. Build default: any signed-in active person may ask about any active person."""
    _window(start, end)
    ids = list(dict.fromkeys(int(i) for i in person_ids))
    if len(ids) > MAX_PEOPLE:
        raise ValidationError(f"At most {MAX_PEOPLE} people at once.")
    people = {p.pk for p in _live_people(ids)}
    out = {pk: [] for pk in people}
    for pk in people:
        rows = (
            Event.objects.filter(status=Event.CONFIRMED, start_utc__lt=end, end_utc__gt=start)
            .filter(
                Q(organizer_id=pk) | Q(pk__in=Guest.objects.filter(person_id=pk).values("event_id"))
            )
            .exclude(pk__in=_declined(pk))
            .values_list("start_utc", "end_utc")
            .order_by("start_utc")
        )
        merged = []
        for s, e in rows:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        out[pk] = merged
    return out


def get_event(actor, public_id):
    """Event as the viewer may see it, or 404. Guests see the guest list (build default)."""
    ev = Event.objects.filter(public_id=public_id).first()
    if ev is None or not _visible(ev, actor):
        raise Http404
    return ev
