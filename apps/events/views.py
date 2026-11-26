import datetime as dt
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.functions import Upper
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.foundation.throttle import throttled
from apps.events import services as es
from apps.events.models import Guest

DEFAULT_TZ = "Asia/Kolkata"  # build default, the site's configured zone
WRITE_LIMIT = 30  # changes per person per minute
AGENDA_DAYS = 30


def _dt(raw):
    try:
        return dt.datetime.strptime((raw or "").strip(), "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValidationError("Enter the start and end as date and time.") from None


def _ids_to_pks(raw):
    """Employee IDs, comma separated, to user pks. An unknown ID is an error, not skipped."""
    ids = [p.strip().upper() for p in (raw or "").replace("\n", ",").split(",") if p.strip()]
    if len(ids) > es.MAX_GUESTS:
        raise ValidationError(f"At most {es.MAX_GUESTS} guests.")
    User = get_user_model()
    found = dict(
        User.objects.annotate(u=Upper("employee_id")).filter(u__in=ids).values_list("u", "pk")
    )
    missing = [i for i in ids if i not in found]
    if missing:
        raise ValidationError(es.GUEST_ERROR)
    return [found[i] for i in ids]


def _limited(request):
    if throttled(request.user, "events-write", WRITE_LIMIT):
        messages.error(request, "Too many changes in a minute. Wait and try again.")
        return True
    return False


def _local(ev, tz_name):
    tz = ZoneInfo(tz_name)
    return ev.start_utc.astimezone(tz), ev.end_utc.astimezone(tz)


@login_required
def home(request):
    now = timezone.now()
    evs = list(
        es.events_for(
            request.user, now - dt.timedelta(hours=12), now + dt.timedelta(days=AGENDA_DAYS)
        )
    )
    rows = []
    for e in evs:
        s, f = _local(e, e.tz)
        mine = Guest.objects.filter(event=e, person=request.user).first()
        rows.append({"e": e, "start": s, "end": f, "response": mine.response if mine else None})
    return render(
        request, "events/home.html", {"rows": rows, "tz": DEFAULT_TZ, "days": AGENDA_DAYS}
    )


MONTH_CAP = 3  # events named per day cell, the rest are counted


def _month_arg(raw):
    """YYYY-MM or None (this month). Anything else is a 400, not a silent fallback."""
    if raw in (None, ""):
        return None
    try:
        d = dt.datetime.strptime(raw, "%Y-%m")
    except ValueError:
        raise ValidationError("Month must look like 2026-12.") from None
    if not 2000 <= d.year <= 2100:
        raise ValidationError("Month must be between 2000 and 2100.")
    return d.date()


def _date_arg(raw):
    """YYYY-MM-DD or None (today). Anything else is a 400, not a silent fallback."""
    if raw in (None, ""):
        return None
    try:
        d = dt.datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError("Date must look like 2026-12-15.") from None
    if not 2000 <= d.year <= 2100:
        raise ValidationError("Date must be between 2000 and 2100.")
    return d


def _by_day(user, first, ndays):
    """{local date: [{e, start, end, time, ...}]} for ndays from first, own calendar, full list."""
    tz = ZoneInfo(DEFAULT_TZ)
    lo = dt.datetime.combine(first, dt.time.min, tzinfo=tz)
    hi = dt.datetime.combine(first + dt.timedelta(days=ndays), dt.time.min, tzinfo=tz)
    cells = {first + dt.timedelta(days=i): [] for i in range(ndays)}
    for e in es.events_for(user, lo, hi):
        s, f = e.start_utc.astimezone(tz), e.end_utc.astimezone(tz)
        d, last = s.date(), max(s, f - dt.timedelta(microseconds=1)).date()  # a zero-length row (the service refuses it) stays on its own day
        while d <= last:
            if d in cells:
                first_day = d == s.date()
                cells[d].append(
                    {
                        "e": e,
                        "time": s.strftime("%H:%M") if first_day else "cont.",
                        "when": f"{s:%a %-d %b %H:%M} to {f:%a %-d %b %H:%M}",
                        "label": (
                            f"{e.title}, continues from an earlier day, until {f:%a %-d %b %H:%M}"
                            if not first_day
                            else f"{e.title}, {s:%a %-d %b %H:%M} to {f:%a %-d %b %H:%M}"
                        ),
                    }
                )
            d += dt.timedelta(days=1)
    return cells


def _range_view(request, ndays, template):
    tz = ZoneInfo(DEFAULT_TZ)
    try:
        d = _date_arg(request.GET.get("d"))
    except ValidationError as exc:
        return render(request, template, {"error": " ".join(exc.messages)}, status=400)
    if d is None:
        d = timezone.now().astimezone(tz).date()
    if ndays == 7:
        d = d - dt.timedelta(days=d.weekday())
    cells = _by_day(request.user, d, ndays)
    today = timezone.now().astimezone(tz).date()
    days = [{"date": k, "items": v, "today": k == today} for k, v in cells.items()]
    return render(
        request,
        template,
        {
            "days": days,
            "start": d,
            "end": d + dt.timedelta(days=ndays - 1),
            "prev": (d - dt.timedelta(days=ndays)).isoformat(),
            "next": (d + dt.timedelta(days=ndays)).isoformat(),
            "month": d.strftime("%Y-%m"),
            "tz": DEFAULT_TZ,
        },
    )


@login_required
def week_view(request):
    """Monday to Sunday, every event of every day listed in full (no cap)."""
    return _range_view(request, 7, "events/week.html")


@login_required
def day_view(request):
    """One day, every event listed in full with its full time range."""
    return _range_view(request, 1, "events/day.html")


@login_required
def month_view(request):
    """Month grid of the person's own calendar (same events as the agenda), Monday first, days
    in the site zone. An event is placed on every local day it touches."""
    tz = ZoneInfo(DEFAULT_TZ)
    try:
        first = _month_arg(request.GET.get("m"))
    except ValidationError as exc:
        return render(request, "events/month.html", {"error": " ".join(exc.messages)}, status=400)
    if first is None:
        first = timezone.now().astimezone(tz).date().replace(day=1)
    grid_start = first - dt.timedelta(days=first.weekday())
    nxt = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    weeks = ((nxt - grid_start).days + 6) // 7
    cells = _by_day(request.user, grid_start, 7 * weeks)
    today = timezone.now().astimezone(tz).date()
    days = [
        {
            "date": d,
            "other": d.month != first.month,
            "today": d == today,
            "shown": v[:MONTH_CAP],
            "more": max(0, len(v) - MONTH_CAP),
        }
        for d, v in cells.items()
    ]
    prev = (first - dt.timedelta(days=1)).replace(day=1)
    return render(
        request,
        "events/month.html",
        {
            "weeks": [days[i : i + 7] for i in range(0, len(days), 7)],
            "first": first,
            "prev": prev.strftime("%Y-%m"),
            "next": nxt.strftime("%Y-%m"),
            "tz": DEFAULT_TZ,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def new_event(request):
    ctx = {"tz": DEFAULT_TZ, "form": {"tz": DEFAULT_TZ}, "action": "Create event"}
    if request.method == "POST":
        ctx["form"] = request.POST
        if _limited(request):
            return render(request, "events/form.html", ctx, status=429)
        try:
            ev = es.create_event(
                request.user,
                request.POST.get("title"),
                _dt(request.POST.get("start")),
                _dt(request.POST.get("end")),
                request.POST.get("tz", DEFAULT_TZ),
                _ids_to_pks(request.POST.get("guests")),
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return render(request, "events/form.html", ctx, status=400)
        except PermissionDenied:
            return render(request, "events/form.html", ctx, status=403)
        messages.success(
            request,
            "Event created. Guests see it on their own calendar; nobody is emailed or notified.",
        )
        return redirect("events:detail", event_id=ev.public_id)
    return render(request, "events/form.html", ctx)


@login_required
def detail(request, event_id):
    ev = es.get_event(request.user, event_id)
    s, f = _local(ev, ev.tz)
    guests = list(ev.guests.select_related("person").order_by("person__employee_id"))
    mine = next((g for g in guests if g.person_id == request.user.pk), None)
    return render(
        request,
        "events/detail.html",
        {
            "e": ev,
            "start": s,
            "end": f,
            "guests": guests,
            "mine": mine,
            "is_organizer": ev.organizer_id == request.user.pk,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def edit(request, event_id):
    ev = es.get_event(request.user, event_id)
    if ev.organizer_id != request.user.pk:
        from django.http import Http404

        raise Http404
    s, f = _local(ev, ev.tz)
    form = {
        "title": ev.title,
        "start": s.strftime("%Y-%m-%dT%H:%M"),
        "end": f.strftime("%Y-%m-%dT%H:%M"),
        "tz": ev.tz,
        "version": ev.version,
    }
    ctx = {"e": ev, "form": form, "action": "Save changes", "editing": True, "tz": ev.tz}
    if request.method == "POST":
        ctx["form"] = request.POST
        if _limited(request):
            return render(request, "events/form.html", ctx, status=429)
        try:
            es.update_event(
                request.user,
                event_id,
                request.POST.get("version", ""),
                request.POST.get("title"),
                _dt(request.POST.get("start")),
                _dt(request.POST.get("end")),
                request.POST.get("tz", ev.tz),
            )
        except es.StaleEvent:
            fresh = es.get_event(request.user, event_id)
            ctx["conflict"] = fresh
            return render(request, "events/form.html", ctx, status=409)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return render(request, "events/form.html", ctx, status=400)
        messages.success(
            request, "Event saved. Guests keep their earlier answers and are not notified."
        )
        return redirect("events:detail", event_id=event_id)
    return render(request, "events/form.html", ctx)


@login_required
@require_POST
def respond_view(request, event_id):
    if _limited(request):
        return redirect("events:detail", event_id=event_id)
    try:
        es.respond(request.user, event_id, request.POST.get("response", ""))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("events:detail", event_id=event_id)


@login_required
@require_http_methods(["GET", "POST"])
def cancel_view(request, event_id):
    ev = es.get_event(request.user, event_id)
    if ev.organizer_id != request.user.pk:
        from django.http import Http404

        raise Http404
    if request.method == "POST" and request.POST.get("confirm") == "yes":
        if not _limited(request):
            es.cancel_event(request.user, event_id)
            messages.success(
                request, "Event cancelled. It stays visible to guests, marked cancelled."
            )
        return redirect("events:detail", event_id=event_id)
    return render(request, "events/cancel.html", {"e": ev})


@login_required
def busy_view(request):
    raw_day = request.GET.get("date", "")
    ctx = {
        "tz": DEFAULT_TZ,
        "date": raw_day,
        "people": request.GET.get("people", ""),
        "result": None,
    }
    if raw_day and request.GET.get("people"):
        try:
            day = dt.datetime.strptime(raw_day, "%Y-%m-%d")
            tz = ZoneInfo(DEFAULT_TZ)
            start = day.replace(tzinfo=tz)
            end = (day + dt.timedelta(days=1)).replace(tzinfo=tz)
            pks = _ids_to_pks(request.GET.get("people"))
            busy = es.free_busy(request.user, pks, start, end)
            names = {u.pk: u for u in get_user_model().objects.filter(pk__in=pks)}
            ctx["result"] = [
                (
                    names[pk].employee_id,
                    [(s.astimezone(tz), e.astimezone(tz)) for s, e in busy.get(pk, [])],
                )
                for pk in pks
            ]
        except (ValueError, ValidationError) as exc:
            messages.error(
                request, " ".join(getattr(exc, "messages", ["Enter a date as YYYY-MM-DD."]))
            )
    return render(request, "events/busy.html", ctx)
