import uuid
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.functions import Upper
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.chat import services as cs
from apps.core.foundation.throttle import throttled

SEND_LIMIT = 60  # messages per person per minute
CHANGE_LIMIT = 20  # new conversations per person per minute


def _pks(raw):
    ids = [p.strip().upper() for p in (raw or "").replace("\n", ",").split(",") if p.strip()]
    if len(ids) > cs.MAX_GROUP_OTHERS:
        raise ValidationError(f"At most {cs.MAX_GROUP_OTHERS} other people.")
    found = dict(
        get_user_model()
        .objects.annotate(u=Upper("employee_id"))
        .filter(u__in=ids)
        .values_list("u", "pk")
    )
    if any(i not in found for i in ids):
        raise ValidationError(cs.PEOPLE_ERROR)
    return [found[i] for i in ids]


def active_only(view):
    """Every chat route refuses a person who is no longer active, with the same 404 as a
    conversation that does not exist."""

    @wraps(view)
    def wrapped(request, *a, **k):
        if cs._live(request.user.pk) is None:
            raise Http404
        return view(request, *a, **k)

    return wrapped


@login_required
@active_only
def home(request):
    return render(request, "chat/home.html", {"spaces": cs.spaces_for(request.user)})


@login_required
@active_only
@require_POST
def start_dm(request):
    if throttled(request.user, "chat-new", CHANGE_LIMIT):
        messages.error(request, "Too many changes in a minute. Wait and try again.")
        return redirect("chat:home")
    try:
        (pk,) = _pks(request.POST.get("person")) or (None,)
        if pk is None:
            raise ValidationError("Enter one employee ID.")
        sp = cs.open_dm(request.user, pk)
    except (ValidationError, ValueError) as exc:
        messages.error(request, " ".join(getattr(exc, "messages", ["Enter one employee ID."])))
        return redirect("chat:home")
    return redirect("chat:space", space_id=sp.public_id)


@login_required
@active_only
@require_POST
def new_group(request):
    if throttled(request.user, "chat-new", CHANGE_LIMIT):
        messages.error(request, "Too many changes in a minute. Wait and try again.")
        return redirect("chat:home")
    try:
        sp = cs.create_group(
            request.user, request.POST.get("name"), _pks(request.POST.get("people"))
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("chat:home")
    return redirect("chat:space", space_id=sp.public_id)


def _label(sp, user):
    if sp.kind == sp.DM:
        for s in cs.spaces_for(user):
            if s["space"].pk == sp.pk:
                return s["label"]
    return sp.name


def _int(raw):
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _page(request, space_id, *, status=200, draft="", client_msg_id=None):
    """The conversation page. Reading marks messages read only when they were all displayed."""
    sp, rows = cs.window(
        request.user,
        space_id,
        before=_int(request.GET.get("before")),
        after=_int(request.GET.get("after")),
    )
    viewing_history = request.GET.get("before") not in (None, "")
    first = rows[0].seq if rows else 0
    last = rows[-1].seq if rows else 0
    if rows and not viewing_history:
        cs.mark_read(request.user, space_id, last, first)
    return render(
        request,
        "chat/space.html",
        {
            "space": sp,
            "label": _label(sp, request.user),
            "rows": rows,
            "last": last,
            "older": first if first > 1 else None,
            "newer": last if last < sp.last_seq else None,
            "client_msg_id": client_msg_id or uuid.uuid4().hex,
            "draft": draft,
            "me": request.user,
            "live": not viewing_history and last >= sp.last_seq,
        },
        status=status,
    )


@login_required
@active_only
def space_view(request, space_id):
    return _page(request, space_id)


@login_required
@active_only
@require_POST
def send(request, space_id):
    body = request.POST.get("body", "")
    cid = request.POST.get("client_msg_id", "")
    if throttled(request.user, "chat-send", SEND_LIMIT):
        messages.error(
            request,
            "You are sending too fast. Your text is kept below. Wait a moment and send again.",
        )
        return _page(request, space_id, status=429, draft=body, client_msg_id=cid or None)
    try:
        cs.post_message(request.user, space_id, body, cid)
    except cs.ReplayConflict as exc:
        messages.error(request, " ".join(exc.messages))
        return _page(request, space_id, status=409, draft=body)  # a fresh client id
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return _page(request, space_id, status=400, draft=body, client_msg_id=cid or None)
    except PermissionDenied:
        return redirect("chat:home")
    return redirect("chat:space", space_id=space_id)


POLL_LIMIT = 30  # polls per person per minute


def poll(request, space_id):
    """New messages after a seq, as a fragment. Answers 401 (never a login page) when the
    session is gone, so the page's script can stop instead of swapping a sign-in page in."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    if cs._live(request.user.pk) is None:
        raise Http404
    if throttled(request.user, "chat-poll", POLL_LIMIT):
        return HttpResponse(status=429)
    after = _int(request.GET.get("after")) or 0
    sp, rows, more = cs.messages_after(request.user, space_id, after)
    if rows:
        cs.mark_read(request.user, space_id, rows[-1].seq, after + 1)
    resp = render(request, "chat/_messages.html", {"rows": rows, "me": request.user})
    resp["X-Chat-Last"] = str(rows[-1].seq if rows else after)
    resp["X-Chat-More"] = "1" if more else "0"
    resp["Cache-Control"] = "no-store"
    return resp
