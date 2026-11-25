import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.functions import Upper
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


@login_required
def home(request):
    return render(request, "chat/home.html", {"spaces": cs.spaces_for(request.user)})


@login_required
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


@login_required
def space_view(request, space_id):
    sp, rows = cs.latest_messages(request.user, space_id)
    last = rows[-1].seq if rows else 0
    cs.mark_read(request.user, space_id, last)
    return render(
        request,
        "chat/space.html",
        {
            "space": sp,
            "label": _label(sp, request.user),
            "rows": rows,
            "last": last,
            "client_msg_id": uuid.uuid4().hex,
            "me": request.user,
        },
    )


@login_required
@require_POST
def send(request, space_id):
    if throttled(request.user, "chat-send", SEND_LIMIT):
        messages.error(request, "You are sending too fast. Wait a moment and send again.")
        return redirect("chat:space", space_id=space_id)
    try:
        cs.post_message(
            request.user, space_id, request.POST.get("body"), request.POST.get("client_msg_id")
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except PermissionDenied:
        return redirect("chat:home")
    return redirect("chat:space", space_id=space_id)


@login_required
def poll(request, space_id):
    """Messages after the given seq, as a fragment the page swaps in every 10 seconds."""
    try:
        after = int(request.GET.get("after", "0"))
    except ValueError:
        after = 0
    sp, rows, more = cs.messages_after(request.user, space_id, after)
    if rows:
        cs.mark_read(request.user, space_id, rows[-1].seq)
    return render(
        request,
        "chat/_messages.html",
        {
            "space": sp,
            "rows": rows,
            "me": request.user,
            "last": rows[-1].seq if rows else after,
            "more": more,
        },
    )
