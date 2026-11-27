"""Calendar export as an iCalendar (RFC 5545) file. Read-only and private: a person's own events
(organised or invited, not cancelled, not declined) that have not ended. Guests, answers and the
organiser are never written, so the file holds nothing the person cannot already see about their own
events and a calendar app cannot send invitations from it.

Faithfulness rules (nothing is dropped or changed silently):
- a single event is written in UTC; an all-day event as DATE values (end is the day after the last day);
- a repeating event keeps its own time zone (TZID plus a VTIMEZONE built from the zone rules) so a 10:00 slot
  stays 10:00 across a clock change; daily, weekly (weekdays, week starts Monday) and monthly (BYMONTHDAY,
  months without that day are skipped, as in the app) become one RRULE with COUNT or UNTIL;
- a skipped occurrence becomes an EXDATE;
- a series with an occurrence whose local time does not exist (a clock change gap) cannot be written as a
  rule without changing the count, so it is written as its real occurrence instants (RDATE) instead;
- more than MAX_EVENTS events is refused with a plain message, never cut."""

import datetime as dt
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db.models import Q

from apps.events import recurrence as rec
from apps.events.models import Event, EventException, Guest

MAX_EVENTS = 2000
UTC = dt.timezone.utc
DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _esc(text):
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
        .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    )  # fmt: skip


def _fold(line):
    """Lines of at most 75 octets; each continuation line starts with one space (counted), never inside a character."""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts, cur, size, limit = [], "", 0, 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            parts.append(cur)
            cur, size, limit = "", 0, 74
        cur += ch
        size += n
    parts.append(cur)
    return "\r\n ".join(parts)


def _u(t):
    return t.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _local(t, tz):
    return t.astimezone(tz).strftime("%Y%m%dT%H%M%S")


def _off(delta):
    s = int(delta.total_seconds())
    sign = "+" if s >= 0 else "-"
    s = abs(s)
    return f"{sign}{s // 3600:02d}{s % 3600 // 60:02d}" + (f"{s % 60:02d}" if s % 60 else "")


def _vtimezone(name, lo, hi):
    """A VTIMEZONE for [lo, hi] (aware datetimes): the offset in force at lo and every change up to hi."""
    tz = ZoneInfo(name)
    step = dt.timedelta(hours=6)
    t, prev = lo - dt.timedelta(days=1), (lo - dt.timedelta(days=1)).astimezone(tz)
    first_off = prev.utcoffset()
    lines = ["BEGIN:VTIMEZONE", f"TZID:{name}"]
    kind = "DAYLIGHT" if prev.dst() else "STANDARD"
    lines += [f"BEGIN:{kind}", "DTSTART:19700101T000000", f"TZOFFSETFROM:{_off(first_off)}",
              f"TZOFFSETTO:{_off(first_off)}", f"TZNAME:{prev.tzname()}", f"END:{kind}"]  # fmt: skip
    end = hi + dt.timedelta(days=1)
    while t < end:
        nxt = t + step
        if nxt.astimezone(tz).utcoffset() != prev.utcoffset():
            a, b = t, nxt  # find the change to the minute
            while b - a > dt.timedelta(minutes=1):
                mid = a + (b - a) / 2
                if mid.astimezone(tz).utcoffset() == prev.utcoffset():
                    a = mid
                else:
                    b = mid
            b = b.replace(second=0, microsecond=0)
            if b.astimezone(tz).utcoffset() == prev.utcoffset():
                b += dt.timedelta(minutes=1)
            after = b.astimezone(tz)
            kind = "DAYLIGHT" if after.dst() else "STANDARD"
            before_wall = (b + prev.utcoffset()).replace(tzinfo=None)  # wall time just before the change
            lines += [f"BEGIN:{kind}", f"DTSTART:{before_wall:%Y%m%dT%H%M%S}",
                      f"TZOFFSETFROM:{_off(prev.utcoffset())}", f"TZOFFSETTO:{_off(after.utcoffset())}",
                      f"TZNAME:{after.tzname()}", f"END:{kind}"]  # fmt: skip
            prev = after
        t = nxt
    lines.append("END:VTIMEZONE")
    return lines


def _rrule(ev, dates):
    parts = [f"FREQ={ev.freq.upper()}", f"INTERVAL={ev.interval}"]
    if ev.freq == "weekly":
        parts.append("BYDAY=" + ",".join(DAYS[w] for w in rec.parse_weekdays(ev.weekdays)))
        parts.append("WKST=MO")
    if ev.freq == "monthly":
        parts.append(f"BYMONTHDAY={dates[0].day}")
    if ev.count is not None:
        parts.append(f"COUNT={ev.count}")
    else:
        o = rec.occurrence(ev, dates[-1])
        parts.append(f"UNTIL={_u(o[0])}")  # the last real occurrence start, in UTC as the rule needs
    return "RRULE:" + ";".join(parts)


def _event_lines(ev, skipped):
    tz = ZoneInfo(ev.tz)
    out = ["BEGIN:VEVENT", f"UID:{ev.public_id}@salow", f"DTSTAMP:{_u(ev.updated_at)}",
           f"LAST-MODIFIED:{_u(ev.updated_at)}", f"SEQUENCE:{ev.version - 1}"]  # fmt: skip
    needs_tz = None
    if ev.all_day:
        out += [f"DTSTART;VALUE=DATE:{ev.start_utc.astimezone(tz):%Y%m%d}",
                f"DTEND;VALUE=DATE:{ev.end_utc.astimezone(tz):%Y%m%d}"]  # fmt: skip
    elif not ev.freq:
        out += [f"DTSTART:{_u(ev.start_utc)}", f"DTEND:{_u(ev.end_utc)}"]
    else:
        dates = list(rec.local_dates(ev))
        occ = {d: rec.occurrence(ev, d) for d in dates}
        gap = any(o is None for o in occ.values())
        if gap:  # the rule cannot say it exactly: list the real occurrences
            real = [occ[d] for d in dates if occ[d] is not None and d not in skipped]
            if not real:
                return [], None
            out += [f"DTSTART:{_u(real[0][0])}", f"DTEND:{_u(real[0][1])}"]
            if len(real) > 1:
                out += ["RDATE:" + ",".join(_u(o[0]) for o in real[1:])]
            # each later occurrence has the same length, so one DTEND/DTSTART pair gives the duration
        else:
            needs_tz = ev.tz
            out += [f"DTSTART;TZID={ev.tz}:{_local(ev.start_utc, tz)}",
                    f"DTEND;TZID={ev.tz}:{_local(ev.end_utc, tz)}", _rrule(ev, dates)]  # fmt: skip
            ex = sorted(d for d in skipped if d in occ)
            if len(ex) == len(dates):
                return [], None  # every occurrence was cancelled: nothing left to export
            if ex:
                out.append(f"EXDATE;TZID={ev.tz}:" + ",".join(_local(occ[d][0], tz) for d in ex))
    out.append("SUMMARY:" + _esc(ev.title))
    if ev.location:
        out.append("LOCATION:" + _esc(ev.location))
    if ev.description:
        out.append("DESCRIPTION:" + _esc(ev.description))
    out += ["STATUS:CONFIRMED", "END:VEVENT"]
    return out, needs_tz


def export_events(user, now):
    """The .ics text (CRLF lines) of the user's own upcoming and ongoing events."""
    mine = Q(organizer=user) | Q(pk__in=Guest.objects.filter(person=user).values("event_id"))
    declined = Guest.objects.filter(person=user, response=Guest.DECLINED).values("event_id")
    base = Event.objects.filter(status=Event.CONFIRMED).filter(mine).exclude(pk__in=declined)
    rows = list(
        base.filter(Q(freq="", end_utc__gt=now) | (~Q(freq="") & Q(last_end_utc__gt=now)))
        .order_by("start_utc", "pk")[: MAX_EVENTS + 1]
    )
    if len(rows) > MAX_EVENTS:
        raise ValidationError(
            f"You have more than {MAX_EVENTS} upcoming events, which is too many for one file. "
            "Nothing was exported."
        )
    skipped = {}
    for x in EventException.objects.filter(event_id__in=[e.pk for e in rows if e.freq]):
        skipped.setdefault(x.event_id, set()).add(x.local_date)
    body, zones = [], {}
    for ev in rows:
        lines, tzname = _event_lines(ev, skipped.get(ev.pk, set()))
        body += lines
        if tzname:
            lo, hi = zones.get(tzname, (ev.start_utc, ev.last_end_utc))
            zones[tzname] = (min(lo, ev.start_utc), max(hi, ev.last_end_utc))
    head = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Salow//Workspace Calendar//EN",
            "CALSCALE:GREGORIAN", "X-WR-CALNAME:Salow calendar"]  # fmt: skip
    for name in sorted(zones):
        head += _vtimezone(name, *zones[name])
    lines = head + body + ["END:VCALENDAR"]
    return "".join(_fold(x) + "\r\n" for x in lines), len(rows)
