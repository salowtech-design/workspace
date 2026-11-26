import uuid
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.functions import Upper
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
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


def _page(request, space_id, *, status=200, draft="", client_msg_id=None, thread_root=None):
    """The conversation page. Reading marks messages read only when they were all displayed."""
    sp, rows = cs.window(
        request.user,
        space_id,
        before=_int(request.GET.get("before")),
        after=_int(request.GET.get("after")),
    )
    viewing_history = request.GET.get("before") not in (None, "")
    cs.attach_reactions(rows, request.user)
    cs.attach_threads(sp, rows)
    reply_to = None
    rt = _int(request.GET.get("reply")) if thread_root is None else thread_root
    if rt:
        reply_to = next(iter(cs.attach_threads(sp, [m for m in cs.Message.objects.filter(space=sp, seq=rt, thread_root_seq__isnull=True)])), None)
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
            "first": first,
            "older": first if first > 1 else None,
            "newer": last if last < sp.last_seq else None,
            "client_msg_id": client_msg_id or uuid.uuid4().hex,
            "draft": draft,
            "reply_to": reply_to,
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
        cs.post_message(request.user, space_id, body, cid, request.POST.get("thread_root_seq"))
    except cs.ReplayConflict as exc:
        messages.error(request, " ".join(exc.messages))
        return _page(request, space_id, status=409, draft=body, thread_root=_int(request.POST.get("thread_root_seq")))  # a fresh client id
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return _page(request, space_id, status=400, draft=body, client_msg_id=cid or None, thread_root=_int(request.POST.get("thread_root_seq")))
    except PermissionDenied:
        return redirect("chat:home")
    return redirect("chat:space", space_id=space_id)


THREAD_LIMIT = 60  # thread page views per person per minute


@login_required
@active_only
def thread_view(request, space_id, root_seq):
    """One first-level message with its replies and a reply form (same members as the conversation)."""
    if throttled(request.user, "chat-thread", THREAD_LIMIT):
        return HttpResponse(status=429)
    sp, root, replies = cs.thread(request.user, space_id, root_seq)
    rows = [root] + replies
    cs.attach_reactions(rows, request.user)
    cs.attach_threads(sp, rows)
    return render(
        request,
        "chat/thread.html",
        {"space": sp, "label": _label(sp, request.user), "root": root, "replies": rows[1:], "rows": rows,
         "me": request.user, "client_msg_id": uuid.uuid4().hex},
    )


REACT_LIMIT = 60  # reaction changes per person per minute
REACT_POLL_LIMIT = 30  # reaction refreshes per person per minute


def _want(raw):
    return {"1": True, "0": False}.get(raw)  # anything else toggles


@login_required
@active_only
@require_POST
def react(request, space_id):
    """Set a reaction. The page's script sends X-Chat-Fetch and gets JSON (so a half-typed message is never
    lost to a reload). Without the script the form posts normally and is redirected back."""
    js = request.headers.get("X-Chat-Fetch") == "1"
    if throttled(request.user, "chat-react", REACT_LIMIT):
        if js:
            return JsonResponse({"error": "Too many reactions in a minute. Wait and try again."}, status=429)
        messages.error(request, "Too many reactions in a minute. Wait and try again.")
        return redirect("chat:space", space_id=space_id)
    seq = request.POST.get("seq")
    try:
        cs.react(request.user, space_id, seq, request.POST.get("kind", ""), _want(request.POST.get("want")))
    except ValidationError as exc:
        if js:
            return JsonResponse({"error": " ".join(exc.messages)}, status=400)
        messages.error(request, " ".join(exc.messages))
    if js:
        out = cs.reactions_in(request.user, space_id, seq, seq)
        return JsonResponse({"seq": int(seq), "reacts": out.get(int(seq), [])})
    return redirect(reverse("chat:space", args=[space_id]) + "#m" + str(_int(seq) or ""))


def reactions(request, space_id):
    """Current reaction counts for the messages on screen (first..last), as JSON. 401 when signed out, like poll."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    if cs._live(request.user.pk) is None:
        raise Http404
    if throttled(request.user, "chat-react-poll", REACT_POLL_LIMIT):
        return HttpResponse(status=429)
    first, last = _int(request.GET.get("first")), _int(request.GET.get("last"))
    if not first or not last or last < first:
        return HttpResponse(status=400)
    out = cs.reactions_in(request.user, space_id, first, last)
    resp = JsonResponse({"reacts": {str(k): v for k, v in out.items()}})
    resp["Cache-Control"] = "no-store"
    return resp


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
    cs.attach_reactions(rows, request.user)
    cs.attach_threads(sp, rows)
    resp = render(request, "chat/_messages.html", {"rows": rows, "me": request.user, "space": sp})
    resp["X-Chat-Last"] = str(rows[-1].seq if rows else after)
    resp["X-Chat-More"] = "1" if more else "0"
    resp["Cache-Control"] = "no-store"
    return resp
