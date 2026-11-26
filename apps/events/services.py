"""Calendar step 1: single events, guests, responses, own view, free/busy.

Build defaults, not user policy (each labelled): any signed-in person may organise an event and
invite any active person; guests are seen by the organiser and by other guests; free/busy (no
titles) is readable for any active person. Nothing notifies anyone and no email is sent: an
invitation only appears on the guest's own calendar. Locking: the event row first, then re-read."""

import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404

from apps.core.foundation.audit import audit
from apps.events import recurrence as rec
from apps.events.models import TITLE_MAX, Event, EventException, Guest

GUEST_ERROR = "Some guests could not be added. Check the employee IDs."  # same for every cause
MAX_GUESTS = 50  # build default
MAX_DURATION = datetime.timedelta(days=14)  # build default
MAX_RANGE = datetime.timedelta(days=62)  # longest window one read may ask for
MAX_PEOPLE = 50  # most people one free/busy read may ask about


class Occurrence:
    """One shown occurrence of an event. Read-only view data: it holds the saved Event as
    .event and never changes it. For a single event the times are the event's own."""

    __slots__ = ("event", "start_utc", "end_utc", "occ_date")

    def __init__(self, event, start_utc, end_utc, occ_date):
        self.event, self.start_utc, self.end_utc, self.occ_date = event, start_utc, end_utc, occ_date

    pk = property(lambda self: self.event.pk)
    public_id = property(lambda self: self.event.public_id)
    title = property(lambda self: self.event.title)
    tz = property(lambda self: self.event.tz)
    organizer_id = property(lambda self: self.event.organizer_id)
    freq = property(lambda self: self.event.freq)


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
        raise ValidationError(GUEST_ERROR)
    return found


SERIES_MAX_DURATION = datetime.timedelta(hours=24)  # build default: a repeating event fits in a day


def _rule(start, end, tz_name, rule):
    """Validated repeat fields for an Event, or {} for no repeat. rule is a dict with freq,
    interval, weekdays (list of ints), count, until (date)."""
    if not rule or not rule.get("freq"):
        return {}
    freq = rule["freq"]
    if freq not in rec.FREQS:
        raise ValidationError("Choose daily, weekly or monthly.")
    try:
        raw_i = rule.get("interval")
        interval = 1 if raw_i in (None, "") else int(raw_i)
    except (TypeError, ValueError):
        raise ValidationError("The repeat interval must be a number.") from None
    if not 1 <= interval <= rec.MAX_INTERVAL:
        raise ValidationError(f"Repeat every 1 to {rec.MAX_INTERVAL}.")
    if end - start > SERIES_MAX_DURATION:
        raise ValidationError("A repeating event can last at most 24 hours.")
    local_day = start.astimezone(_tz(tz_name)).date()
    wds = sorted({int(w) for w in (rule.get("weekdays") or [])})
    if any(not 0 <= w <= 6 for w in wds):
        raise ValidationError("Unknown weekday.")
    if freq == "weekly":
        if not wds:
            wds = [local_day.weekday()]
        if local_day.weekday() not in wds:
            raise ValidationError("The first day of a weekly series must be one of its weekdays.")
    else:
        wds = []
    count, until = rule.get("count"), rule.get("until")
    if (count in (None, "")) == (until in (None, "")):
        raise ValidationError("End the repeat after a number of times or on a date, not both.")
    if count not in (None, ""):
        try:
            count = int(count)
        except (TypeError, ValueError):
            raise ValidationError("The number of times must be a number.") from None
        if not 1 <= count <= rec.MAX_COUNT:
            raise ValidationError(f"Repeat 1 to {rec.MAX_COUNT} times.")
        until = None
    else:
        count = None
        if until < local_day or (until - local_day).days > rec.MAX_SPAN_DAYS:
            raise ValidationError("The end date must be within two years after the first day.")
    return {
        "freq": freq,
        "interval": interval,
        "weekdays": ",".join(str(w) for w in wds),
        "count": count,
        "until": until,
    }


def create_event(actor, title, local_start, local_end, tz_name, guest_ids=(), rule=None):
    title = _title(title)
    start, end = _utc(local_start, local_end, tz_name)
    fields = _rule(start, end, tz_name, rule)
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
        ev = Event(organizer=me, title=title, start_utc=start, end_utc=end, tz=tz_name.strip())
        for k, v in fields.items():
            setattr(ev, k, v)
        if fields:
            ev.last_end_utc = rec.last_end(ev)
        ev.save()
        Guest.objects.bulk_create([Guest(event=ev, person=g) for g in guests])
        audit(me, "events.create", "event", ev.pk, guests=len(guests), repeats=bool(fields))
    return ev


def _lock_event(public_id):
    return Event.objects.select_for_update().filter(public_id=public_id).first()


def _pause():
    """Test hook, called after the checks and before the write. A no-op."""


def _locked(event_public_id, actor):
    """Lock the event row first, re-read the actor, return the live event or raise 404."""
    ev = _lock_event(event_public_id)
    me = get_user_model().objects.filter(pk=actor.pk, is_active=True, status="active").first()
    if ev is None or me is None:
        raise Http404
    return ev, me


def _visible(ev, me):
    return ev.organizer_id == me.pk or Guest.objects.filter(event=ev, person=me).exists()


def update_event(actor, public_id, expected_version, title, local_start, local_end, tz_name):
    """Organiser only. A guest or stranger gets 404 (existence is not revealed)."""
    try:
        version = int(str(expected_version).strip())
    except ValueError:
        raise ValidationError("The form is out of date. Reload the event and try again.") from None
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
        if ev.version != version:
            raise StaleEvent("The event changed since you opened it.")
        if ev.freq:
            old_day = ev.start_utc.astimezone(ZoneInfo(ev.tz)).date()
            new_day = start.astimezone(_tz(tz_name)).date()
            if (old_day != new_day or ev.tz != tz_name.strip()) and ev.exceptions.exists():
                raise ValidationError(
                    "This series has cancelled occurrences, so its first day and time zone "
                    "cannot change. You can change the title and the time of day."
                )
            _rule(start, end, tz_name, {
                "freq": ev.freq, "interval": ev.interval, "weekdays": rec.parse_weekdays(ev.weekdays),
                "count": ev.count, "until": ev.until,
            })  # fmt: skip
        _pause()
        ev.title, ev.start_utc, ev.end_utc, ev.tz = title, start, end, tz_name.strip()
        if ev.freq:
            ev.last_end_utc = rec.last_end(ev)
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
        _pause()
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


def _series_rows(qs_filter, start, end):
    """Single events overlapping the window plus every series whose first occurrence starts
    before the window ends and whose last occurrence ends after it starts (so an old series that
    still runs is found, and an ended one is not)."""
    base = Event.objects.filter(status=Event.CONFIRMED).filter(qs_filter)
    singles = base.filter(freq="", start_utc__lt=end, end_utc__gt=start)
    series = base.exclude(freq="").filter(start_utc__lt=end, last_end_utc__gt=start)
    return list(singles), list(series)


def _occurrences(singles, series, start, end):
    out = [(e, e.start_utc, e.end_utc, None) for e in singles]
    skipped = {}
    if series:
        for x in EventException.objects.filter(event_id__in=[e.pk for e in series]):
            skipped.setdefault(x.event_id, set()).add(x.local_date)
    for e in series:
        for d, s, f in rec.expand(e, start, end, skipped.get(e.pk, frozenset())):
            out.append((e, s, f, d))
    return out


def events_for(user, start, end):
    """Occurrences on the user's own calendar (organised or invited), not cancelled, overlapping
    the window, as Occurrence objects in start order (a repeating event gives one per
    occurrence; occ_date is its local date, None for a single event). Needs an aware window of
    at most 62 days."""
    _window(start, end)
    mine = Q(organizer=user) | Q(pk__in=Guest.objects.filter(person=user).values("event_id"))
    singles, series = _series_rows(mine, start, end)
    declined = set(_declined(user.pk).values_list("event_id", flat=True))
    singles = [e for e in singles if e.pk not in declined]
    series = [e for e in series if e.pk not in declined]
    out = [Occurrence(e, s, f, d) for e, s, f, d in _occurrences(singles, series, start, end)]
    out.sort(key=lambda o: (o.start_utc, o.pk))
    return out


def free_busy(viewer, person_ids, start, end):
    """Busy intervals only (no titles, no guests), per person. A declined or cancelled event is
    not busy; each occurrence of a repeating event is. Build default: any signed-in active person
    may ask about any active person."""
    _window(start, end)
    ids = list(dict.fromkeys(int(i) for i in person_ids))
    if len(ids) > MAX_PEOPLE:
        raise ValidationError(f"At most {MAX_PEOPLE} people at once.")
    people = {p.pk for p in _live_people(ids)}
    out = {pk: [] for pk in people}
    for pk in people:
        flt = Q(organizer_id=pk) | Q(pk__in=Guest.objects.filter(person_id=pk).values("event_id"))
        singles, series = _series_rows(flt, start, end)
        declined = set(_declined(pk).values_list("event_id", flat=True))
        singles = [e for e in singles if e.pk not in declined]
        series = [e for e in series if e.pk not in declined]
        rows = sorted((s, f) for _e, s, f, _d in _occurrences(singles, series, start, end))
        merged = []
        for s, e in rows:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        out[pk] = merged
    return out


def skip_occurrence(actor, public_id, local_date):
    """Organiser cancels one occurrence of a repeating event (identified by its local date in the
    event's zone, which must be a real occurrence). Others get 404."""
    with transaction.atomic():
        ev, me = _locked(public_id, actor)
        if ev.organizer_id != me.pk or not _visible(ev, me):
            raise Http404
        if not ev.freq:
            raise ValidationError("This event does not repeat.")
        if ev.status == Event.CANCELLED:
            raise ValidationError("A cancelled event cannot be changed.")
        if local_date not in set(rec.local_dates(ev)) or rec.occurrence(ev, local_date) is None:
            raise ValidationError("That is not an occurrence of this event.")
        if ev.exceptions.filter(local_date=local_date).exists():
            return ev
        try:
            with transaction.atomic():
                EventException.objects.create(event=ev, local_date=local_date)
        except IntegrityError:
            return ev  # the same occurrence was cancelled at the same time
        ev.version += 1
        ev.save(update_fields=["version", "updated_at"])
        audit(me, "events.skip_occurrence", "event", ev.pk, date=local_date.isoformat())
    return ev


def get_event(actor, public_id):
    """Event as the viewer may see it, or 404. Guests see the guest list (build default)."""
    ev = Event.objects.filter(public_id=public_id).first()
    if ev is None or not _visible(ev, actor):
        raise Http404
    return ev
