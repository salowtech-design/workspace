"""Chat step 1: direct messages and groups, ordered text messages, catch-up, read marks.

Build defaults, not user policy: any active person may start a DM with any active person or make
a group (2 to 50 other people); only a group's manager (its creator) changes it (not built yet).
Nobody, including admins, can read a space they are not in. Order comes from a per-space sequence
assigned under the space row lock. Reactions are a fixed set of six, one row per person and kind.
No edit, delete, retention or attachments exist."""

import logging
import re
import time

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.http import Http404

from apps.chat.models import (
    BODY_MAX,
    NAME_MAX,
    REACTION_KEYS,
    REACTIONS,
    DmPair,
    Member,
    Message,
    Reaction,
    Space,
)
from apps.core.foundation.audit import audit
from apps.core.foundation.notify import notify

MAX_GROUP_OTHERS = 50
PAGE = 100


class ReplayConflict(ValidationError):
    """The same send form was reused for different text."""


REPLAY_MESSAGE = (
    "That form was already used to send a different message. Your text is kept below. "
    "Press Send to send it as a new message."
)
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


_DM_WAIT = (20, 0.1)  # up to 2 seconds for the winning transaction to commit
_CATCH_DM_DUPLICATE = True  # tests switch it off to see the raw error that the fallback absorbs


def _pause_dm():
    """Test hook between the pair lookup and the create. A no-op."""


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
    _pause_dm()
    try:
        with transaction.atomic():
            sp = Space.objects.create(kind=Space.DM, created_by=me)
            Member.objects.bulk_create(
                [Member(space=sp, person=me), Member(space=sp, person=other)]
            )
            DmPair.objects.create(user_low=low, user_high=high, space=sp)
            audit(me, "chat.dm.create", "space", sp.pk)
        return sp
    except (IntegrityError, OperationalError) as exc:
        # a lost race for the same pair surfaces as a duplicate key or, on TiDB, a deadlock (1213)
        lost_race = isinstance(exc, IntegrityError) or (exc.args and exc.args[0] == 1213)
        if not _CATCH_DM_DUPLICATE or not lost_race:
            raise
        # Only a lost race for the same pair is absorbed. The winner may not have committed yet
        # (the deadlock case), so look for its row a few times, briefly; if it never appears the
        # original error is raised, whatever it was.
        for _ in range(_DM_WAIT[0]):
            pair = (
                DmPair.objects.filter(user_low=low, user_high=high).select_related("space").first()
            )
            if pair is not None:
                return pair.space
            time.sleep(_DM_WAIT[1])
        raise


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


def post_message(actor, public_id, body, client_msg_id, thread_root_seq=None):
    body = (body or "").strip()
    cid = (client_msg_id or "").strip()
    if not body:
        raise ValidationError("A message cannot be empty.")
    if len(body) > BODY_MAX:
        raise ValidationError(f"A message can be at most {BODY_MAX} characters.")
    if not 1 <= len(cid) <= 64:
        raise ValidationError("The form is out of date. Reload the conversation.")
    root = None
    if thread_root_seq not in (None, ""):
        if isinstance(thread_root_seq, bool) or not (
            isinstance(thread_root_seq, int)
            or (isinstance(thread_root_seq, str) and thread_root_seq.isascii() and thread_root_seq.isdigit() and len(thread_root_seq) <= 19)
        ):
            raise ValidationError(THREAD_ERROR)  # no floats, no signs, no text
        root = int(thread_root_seq)
        if not 1 <= root <= 2**62:
            raise ValidationError(THREAD_ERROR)
    with transaction.atomic():
        sp = _lock_space(public_id)  # space row first, then everything is re-read
        me = _live(actor.pk)
        mem = _member(sp, me.pk) if sp is not None and me is not None else None
        if mem is None:
            raise Http404  # a non-member gets the same answer as an unknown space
        dup = Message.objects.filter(space=sp, author=me, client_msg_id=cid).first()
        if dup is not None:
            if dup.body != body or dup.thread_root_seq != root:
                raise ReplayConflict(REPLAY_MESSAGE)  # never drop new text silently
            return dup  # a retry of the same send: the original, no second message
        if root is not None:
            # the root must be an existing first-level message of this same space (one level only)
            if not Message.objects.filter(space=sp, seq=root, thread_root_seq__isnull=True).exists():
                raise ValidationError(THREAD_ERROR)
        _pause()
        seq = sp.last_seq + 1
        msg = Message.objects.create(
            space=sp, seq=seq, author=me, body=body, client_msg_id=cid, thread_root_seq=root
        )
        Space.objects.filter(pk=sp.pk).update(last_seq=seq, last_message_at=msg.created_at)
        if mem.last_read_seq >= seq - 1:  # only a caught-up sender has "read" up to here
            Member.objects.filter(pk=mem.pk).update(last_read_seq=seq)
        audit(me, "chat.message.post", "space", sp.pk, seq=seq)  # ids only, never the text
        notify_mentions(sp, msg, me)
    return msg


log = logging.getLogger(__name__)
MENTION = re.compile(r"(?<![A-Za-z0-9_@-])@([A-Za-z0-9_-]{1,32})")
MENTION_MAX = 20  # most people one message can notify


def mentioned_members(sp, body, author):
    """People this message mentions as @EmployeeID who are active members of this space now. Not the author, not
    someone who left, is suspended or is not active, and not anyone outside the space (an ID that matches no
    member is ignored without saying so). Matching is case-insensitive on the employee ID."""
    ids = []
    for m in MENTION.finditer(body or ""):
        eid = m.group(1).upper()
        if eid not in ids:
            ids.append(eid)
        if len(ids) >= MENTION_MAX:
            break
    if not ids:
        return []
    out = []
    rows = Member.objects.filter(space=sp, left_at__isnull=True).select_related("person")
    by_id = {r.person.employee_id.upper(): r.person for r in rows}
    for eid in ids:
        p = by_id.get(eid)
        if p is not None and p.pk != author.pk and p.is_active and p.status == "active":
            out.append(p)
    return out


def mention_choices(sp, user):
    """Active members the composer offers for @mentions: everyone in the space except the viewer."""
    rows = Member.objects.filter(space=sp, left_at__isnull=True).exclude(person=user).select_related("person")
    return sorted(
        (r.person for r in rows if r.person.is_active and r.person.status == "active"),
        key=lambda p: (p.full_name.lower(), p.employee_id),
    )[:MAX_GROUP_OTHERS]


def notify_mentions(sp, msg, author):
    """One in-app notice per mentioned member. The title names the author and the conversation, never the message text.
    The link opens the conversation (the thread for a reply), where the member's own access check still applies.
    A failure here never blocks or undoes the message."""
    people = mentioned_members(sp, msg.body, author)
    if not people:
        return
    if sp.kind == Space.DM:
        where = "a direct message"
    else:
        where = sp.name or "a group"
    from django.urls import reverse

    if msg.thread_root_seq:
        link = reverse("chat:thread", args=[sp.public_id, msg.thread_root_seq]) + f"#m{msg.seq}"
    else:
        link = reverse("chat:space", args=[sp.public_id]) + f"#m{msg.seq}"
    for p in people:
        try:
            with transaction.atomic():
                notify(p, "chat_mention", f"{author.full_name} mentioned you in {where}", link, check=("chat_message", msg))
        except Exception:  # noqa: BLE001
            log.exception("chat mention notice failed")


THREAD_ERROR = "That message cannot be replied to in a thread. Reload the conversation."
THREAD_MAX = 200  # most replies one thread page shows


def attach_threads(sp, rows):
    """Set m.reply_count on first-level messages and m.root on replies (the root's author and a short
    start of its text), for the rows on screen. Only reads this space."""
    rows = list(rows)
    roots = {m.seq for m in rows if m.thread_root_seq is None}
    counts = {}
    if roots:
        from django.db.models import Count

        counts = dict(
            Message.objects.filter(space=sp, thread_root_seq__in=roots)
            .values_list("thread_root_seq")
            .annotate(n=Count("id"))
        )
    need = {m.thread_root_seq for m in rows if m.thread_root_seq is not None}
    heads = {
        m.seq: m for m in Message.objects.filter(space=sp, seq__in=need).select_related("author")
    } if need else {}
    for m in rows:
        m.reply_count = counts.get(m.seq, 0) if m.thread_root_seq is None else 0
        m.root = heads.get(m.thread_root_seq) if m.thread_root_seq is not None else None
    return rows


def thread_page_after(sp, msg):
    """The `after` value of the thread page that holds this reply (0 for the first page)."""
    k = Message.objects.filter(space=sp, thread_root_seq=msg.thread_root_seq, seq__lt=msg.seq).count()
    start = k - k % THREAD_MAX
    if start == 0:
        return 0
    prev = (
        Message.objects.filter(space=sp, thread_root_seq=msg.thread_root_seq)
        .order_by("seq")
        .values_list("seq", flat=True)[start - 1 : start]
    )
    return prev[0] if prev else 0


def thread(actor, public_id, root_seq, after=0):
    """A first-level message and its replies, oldest first (member only; 404 for a non-member or a
    message that is not a first-level message of this space). Returns (space, root, replies after `after`, more)."""
    sp, _ = _visible_space(actor, public_id)
    root = Message.objects.filter(
        space=sp, seq=max(0, int(root_seq)), thread_root_seq__isnull=True
    ).select_related("author").first()
    if root is None:
        raise Http404
    rows = list(
        Message.objects.filter(space=sp, thread_root_seq=root.seq, seq__gt=max(0, int(after)))
        .select_related("author")
        .order_by("seq")[: THREAD_MAX + 1]
    )
    return sp, root, rows[:THREAD_MAX], len(rows) > THREAD_MAX


def _visible_space(actor, public_id):
    sp = Space.objects.filter(public_id=public_id).first()
    if sp is None or _live(actor.pk) is None:
        raise Http404
    mem = _member(sp, actor.pk)
    if mem is None:
        raise Http404
    return sp, mem


def messages_after(actor, public_id, after_seq=0, limit=PAGE):
    """Messages with seq > after_seq, oldest first. 404 for a non-member."""
    sp, _ = _visible_space(actor, public_id)
    limit = max(1, min(int(limit), PAGE))
    rows = list(
        Message.objects.filter(space=sp, seq__gt=max(0, int(after_seq)))
        .select_related("author")
        .order_by("seq")[: limit + 1]
    )
    return sp, rows[:limit], len(rows) > limit


def window(actor, public_id, before=None, after=None):
    """The page of history to show. Returns (space, rows, first_unread_gap).

    before=N: the PAGE messages older than N (history). after=N: the first PAGE after N.
    Neither: the latest PAGE, unless more than PAGE are unread, in which case the page starts at
    the first unread message so nothing unseen is skipped over."""
    sp, mem = _visible_space(actor, public_id)
    qs = Message.objects.filter(space=sp).select_related("author")
    if before is not None:
        rows = list(qs.filter(seq__lt=max(0, int(before))).order_by("-seq")[:PAGE])[::-1]
    elif after is not None:
        rows = list(qs.filter(seq__gt=max(0, int(after))).order_by("seq")[:PAGE])
    elif sp.last_seq - mem.last_read_seq > PAGE:
        rows = list(qs.filter(seq__gt=mem.last_read_seq).order_by("seq")[:PAGE])
    else:
        rows = list(qs.order_by("-seq")[:PAGE])[::-1]
    return sp, rows


def mark_read(actor, public_id, seq, from_seq):
    """Move the read mark forward to seq, never backwards, never past the last message, and only
    when the messages shown started at or before the first unread one (from_seq <= mark + 1), so
    messages that were never displayed are not marked read."""
    with transaction.atomic():
        sp, _ = _visible_space(actor, public_id)
        seq = min(int(seq), sp.last_seq)
        Member.objects.filter(
            space=sp,
            person=actor,
            left_at__isnull=True,
            last_read_seq__lt=seq,
            last_read_seq__gte=int(from_seq) - 1,
        ).update(last_read_seq=seq)


def spaces_for(user):
    """The person's conversations with unread counts (others' messages after the read mark),
    most recently active first."""
    rows = (
        Member.objects.filter(person=user, left_at__isnull=True)
        .select_related("space")
        .order_by("-space__last_message_at", "-space_id")
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
        unread = (
            Message.objects.filter(space=m.space, seq__gt=m.last_read_seq)
            .exclude(author=user)
            .count()
        )
        out.append({"space": m.space, "label": label, "unread": unread})
    return out


def react(actor, public_id, seq, kind, want=None):
    """Set one reaction by the actor on message `seq`. want=None toggles; want=True or False asks for that
    state, so a repeated or stale press (a second tab) is a no-op instead of a flip. Returns the state after.
    A kind outside the fixed set is a ValidationError. Anyone not in the space, an unknown space and an
    unknown message all get the same 404. The space row lock is taken first, as in post_message, so a
    member who leaves cannot slip a reaction in between the check and the write."""
    if kind not in REACTION_KEYS:
        raise ValidationError("That reaction does not exist.")
    try:
        seq = int(seq)
    except (TypeError, ValueError):
        raise Http404
    with transaction.atomic():
        sp = _lock_space(public_id)
        me = _live(actor.pk)
        mem = _member(sp, me.pk) if sp is not None and me is not None else None
        if mem is None:
            raise Http404
        if seq < 1 or seq > sp.last_seq:
            raise Http404  # also keeps a huge number away from the database query
        msg = Message.objects.filter(space=sp, seq=seq).first()
        if msg is None:
            raise Http404
        _pause()
        existing = Reaction.objects.filter(message=msg, person=me, kind=kind)
        if want is True and existing.exists():
            return True
        if want is False and not existing.exists():
            return False
        if existing.exists():
            existing.delete()
            audit(me, "chat.reaction.remove", "space", sp.pk, seq=seq, kind=kind)
            return False
        Reaction.objects.create(message=msg, person=me, kind=kind)
        audit(me, "chat.reaction.add", "space", sp.pk, seq=seq, kind=kind)
        return True


def attach_reactions(rows, user):
    """Set m.reacts on each message: a list of (key, emoji, label, count, mine) for the six kinds, in a fixed order."""
    rows = list(rows)
    if not rows:
        return rows
    counts, mine = {}, set()
    for mid, pid, kind in Reaction.objects.filter(message__in=rows).values_list(
        "message_id", "person_id", "kind"
    ):
        counts[(mid, kind)] = counts.get((mid, kind), 0) + 1
        if pid == user.pk:
            mine.add((mid, kind))
    for m in rows:
        m.reacts = [
            (k, e, label, counts.get((m.pk, k), 0), (m.pk, k) in mine) for k, e, label in REACTIONS
        ]
    return rows


REFRESH_MAX = 200  # most messages one refresh covers


def reactions_in(actor, public_id, first, last):
    """{seq: [(kind, count, mine)...]} for messages first..last (at most REFRESH_MAX of them). 404 for a non-member."""
    sp, _ = _visible_space(actor, public_id)
    first = max(1, min(int(first), sp.last_seq + 1))  # never past the newest message, so no huge number reaches the query
    last = min(int(last), sp.last_seq, first + REFRESH_MAX - 1)
    if last < first:
        return {}
    msgs = list(Message.objects.filter(space=sp, seq__gte=first, seq__lte=last))
    attach_reactions(msgs, actor)
    return {m.seq: [(k, n, mine) for k, _e, _l, n, mine in m.reacts] for m in msgs}
