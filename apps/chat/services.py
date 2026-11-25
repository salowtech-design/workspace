"""Chat step 1: direct messages and groups, ordered text messages, catch-up, read marks.

Build defaults, not user policy: any active person may start a DM with any active person or make
a group (2 to 50 other people); only a group's manager (its creator) changes it (not built yet).
Nobody, including admins, can read a space they are not in. Order comes from a per-space sequence
assigned under the space row lock. No edit, delete, retention or attachments exist."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import Http404

from apps.chat.models import BODY_MAX, NAME_MAX, DmPair, Member, Message, Space
from apps.core.foundation.audit import audit

MAX_GROUP_OTHERS = 50
PAGE = 100
PEOPLE_ERROR = "Some people could not be added. Check the employee IDs."  # same for every cause


def _live(user_pk):
    return get_user_model().objects.filter(pk=user_pk, is_active=True, status="active").first()


def _people(ids, exclude):
    ids = {int(i) for i in ids}
    ids.discard(exclude)
    found = list(get_user_model().objects.filter(pk__in=ids, is_active=True, status="active"))
    if len(found) != len(ids):
        raise ValidationError(PEOPLE_ERROR)
    return found


def open_dm(actor, other_pk):
    """The one DM between two people, created on first use. Returns the space."""
    with transaction.atomic():
        me = _live(actor.pk)
        if me is None:
            raise PermissionDenied("Not allowed.")
        if int(other_pk) == me.pk:
            raise ValidationError("You cannot message yourself.")
        (other,) = _people([other_pk], exclude=me.pk)
        low, high = sorted((me.pk, other.pk))
        pair = DmPair.objects.filter(user_low=low, user_high=high).select_related("space").first()
        if pair is not None:
            return pair.space
    try:
        with transaction.atomic():
            sp = Space.objects.create(kind=Space.DM, created_by=me)
            Member.objects.bulk_create(
                [Member(space=sp, person=me), Member(space=sp, person=other)]
            )
            DmPair.objects.create(user_low=low, user_high=high, space=sp)
            audit(me, "chat.dm.create", "space", sp.pk)
        return sp
    except IntegrityError:  # the other side created it first: use theirs
        return DmPair.objects.select_related("space").get(user_low=low, user_high=high).space


def create_group(actor, name, other_pks):
    name = (name or "").strip()
    if not name or len(name) > NAME_MAX:
        raise ValidationError(f"A group needs a name of 1 to {NAME_MAX} characters.")
    other_pks = list(dict.fromkeys(other_pks))
    if len(other_pks) > MAX_GROUP_OTHERS:
        raise ValidationError(f"At most {MAX_GROUP_OTHERS} other people.")
    with transaction.atomic():
        me = _live(actor.pk)
        if me is None:
            raise PermissionDenied("Not allowed.")
        others = _people(other_pks, exclude=me.pk)
        if len(others) < 2:
            raise ValidationError(
                "A group needs at least two other people. Use a direct message for one."
            )
        sp = Space.objects.create(kind=Space.GROUP, name=name, created_by=me)
        Member.objects.create(space=sp, person=me, role=Member.MANAGER)
        Member.objects.bulk_create([Member(space=sp, person=o) for o in others])
        audit(me, "chat.group.create", "space", sp.pk, members=len(others) + 1)
    return sp


def _lock_space(public_id):
    return Space.objects.select_for_update().filter(public_id=public_id).first()


def _pause():
    """Test hook between the membership check and the write. A no-op."""


def _member(space, user_pk):
    return Member.objects.filter(space=space, person_id=user_pk, left_at__isnull=True).first()


def post_message(actor, public_id, body, client_msg_id):
    body = (body or "").strip()
    cid = (client_msg_id or "").strip()
    if not body:
        raise ValidationError("A message cannot be empty.")
    if len(body) > BODY_MAX:
        raise ValidationError(f"A message can be at most {BODY_MAX} characters.")
    if not 1 <= len(cid) <= 64:
        raise ValidationError("The form is out of date. Reload the conversation.")
    with transaction.atomic():
        sp = _lock_space(public_id)  # space row first, then everything is re-read
        me = _live(actor.pk)
        if sp is None or me is None or _member(sp, me.pk) is None:
            raise Http404  # a non-member gets the same answer as an unknown space
        dup = Message.objects.filter(space=sp, author=me, client_msg_id=cid).first()
        if dup is not None:
            return dup  # a retry of the same send: the original, no second message
        _pause()
        seq = sp.last_seq + 1
        msg = Message.objects.create(space=sp, seq=seq, author=me, body=body, client_msg_id=cid)
        Space.objects.filter(pk=sp.pk).update(last_seq=seq)
        Member.objects.filter(space=sp, person=me).update(last_read_seq=seq)
        audit(me, "chat.message.post", "space", sp.pk, seq=seq)  # ids only, never the text
    return msg


def messages_after(actor, public_id, after_seq=0, limit=PAGE):
    """Messages with seq > after_seq, oldest first. 404 for a non-member."""
    sp = Space.objects.filter(public_id=public_id).first()
    if sp is None or _live(actor.pk) is None or _member(sp, actor.pk) is None:
        raise Http404
    limit = max(1, min(int(limit), PAGE))
    rows = list(
        Message.objects.filter(space=sp, seq__gt=max(0, int(after_seq)))
        .select_related("author")
        .order_by("seq")[: limit + 1]
    )
    return sp, rows[:limit], len(rows) > limit


def latest_messages(actor, public_id, limit=PAGE):
    """The last `limit` messages, oldest first."""
    sp = Space.objects.filter(public_id=public_id).first()
    if sp is None or _live(actor.pk) is None or _member(sp, actor.pk) is None:
        raise Http404
    rows = list(Message.objects.filter(space=sp).select_related("author").order_by("-seq")[:limit])
    return sp, rows[::-1]


def mark_read(actor, public_id, seq):
    """Move the read mark forward only, never past the last message."""
    with transaction.atomic():
        sp = Space.objects.filter(public_id=public_id).first()
        if sp is None or _live(actor.pk) is None or _member(sp, actor.pk) is None:
            raise Http404
        seq = min(int(seq), sp.last_seq)
        Member.objects.filter(
            space=sp, person=actor, left_at__isnull=True, last_read_seq__lt=seq
        ).update(last_read_seq=seq)


def spaces_for(user):
    """The person's conversations with unread counts, most recently active first."""
    rows = (
        Member.objects.filter(person=user, left_at__isnull=True)
        .select_related("space")
        .annotate(unread=F("space__last_seq") - F("last_read_seq"))
        .order_by("-space__last_seq", "-space_id")
    )
    out = []
    for m in rows:
        label = m.space.name
        if m.space.kind == Space.DM:
            other = (
                Member.objects.filter(space=m.space)
                .exclude(person=user)
                .select_related("person")
                .first()
            )
            label = (
                f"{other.person.full_name} ({other.person.employee_id})"
                if other
                else "Direct message"
            )
        out.append({"space": m.space, "label": label, "unread": max(0, m.unread)})
    return out
