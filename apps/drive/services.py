"""Drive operations. Every function checks the foundation permission policy first."""

import logging
import random
import re
import secrets
import time
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.core.foundation.audit import audit
from apps.core.foundation.notify import notify
from apps.core.foundation.permissions import can, require
from apps.drive import manifest as mf
from apps.drive import policy  # noqa: F401  (registers the policies)
from apps.drive.models import (
    DriveChange,
    DriveFile,
    FileVersion,
    Folder,
    ManifestChange,
    ManifestEpoch,
    Share,
)

FOLDER, FILE = "drive.folder", "drive.file"


logger = logging.getLogger(__name__)


def _clean_name(name):
    name = (name or "").strip()
    if (
        not name
        or len(name) > 200
        or "/" in name
        or "\\" in name
        or name in (".", "..")
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
    ):
        raise ValidationError("Enter a name up to 200 characters without slashes.")
    return name


def _journal(user, item, action, subject=None):
    kind = "folder" if isinstance(item, Folder) else "file"
    DriveChange.objects.create(
        owner_id=item.owner_id,
        item_type=kind,
        item_id=item.pk,
        action=action,
        actor=user,
        subject=subject,
    )


def _lock_users(*user_ids):
    """Lock account rows in ascending id order, always, so two writers can never wait on
    each other in a circle (A shares to B while B shares to A)."""
    ids = sorted({i for i in user_ids if i is not None})
    list(get_user_model().objects.select_for_update().filter(pk__in=ids).order_by("pk"))


def _lock_tree(owner_id):
    """One lock per owner. Every write below takes it first, so moves, creates, trashes and
    restores inside one person's tree run one at a time and always read fresh state.
    Plain reads after the lock are current under Django's default READ COMMITTED level
    (a test asserts the level so a settings change cannot silently break this)."""
    _lock_users(owner_id)


def _fresh(item):
    """Re-read an item (and nothing stale) after the tree lock is held."""
    item.refresh_from_db()
    if isinstance(item, Folder):
        item.parent  # noqa: B018  (reload relation lazily from fresh parent_id)
    return item


def _depth(folder):
    n = 0
    while folder is not None and n <= policy.MAX_DEPTH + 1:
        n += 1
        folder = folder.parent
    return n


def _in_trash(folder):
    """True when the folder or any ancestor is in the trash."""
    n = 0
    while folder is not None and n <= policy.MAX_DEPTH + 1:
        if folder.trashed_at is not None:
            return True
        n += 1
        folder = folder.parent
    return False


def _name_taken(parent, name, owner_id, exclude=None):
    folders = Folder.objects.filter(parent=parent, name=name, trashed_at__isnull=True)
    files = DriveFile.objects.filter(folder=parent, name=name, trashed_at__isnull=True)
    if parent is None:
        folders, files = folders.filter(owner_id=owner_id), files.filter(owner_id=owner_id)
    if exclude is not None:
        if isinstance(exclude, Folder):
            folders = folders.exclude(pk=exclude.pk)
        else:
            files = files.exclude(pk=exclude.pk)
    return folders.exists() or files.exists()


@transaction.atomic
def create_folder(user, name, parent=None):
    name = _clean_name(name)
    if parent is not None:
        _lock_tree(parent.owner_id)
        _fresh(parent)
        require(user, "edit", FOLDER, parent)
        if _in_trash(parent) or _depth(parent) >= policy.MAX_DEPTH:
            raise ValidationError("Cannot create a folder here (in the trash or nested too deep).")
    else:
        _lock_tree(user.pk)
    # Build default, not user policy: an item created inside a shared folder belongs to the
    # folder's owner; the creator is recorded separately.
    owner_id = parent.owner_id if parent is not None else user.pk
    if _name_taken(parent, name, owner_id):
        raise ValidationError("That name is already used here.")
    folder = Folder.objects.create(owner_id=owner_id, parent=parent, name=name, created_by=user)
    _journal(user, folder, "create")
    audit(user, "drive.folder.create", FOLDER, folder.pk, parent=getattr(parent, "pk", None))
    return folder


# ---- manifest (see salow-drive-manifest-design.md) ----


def _people(item):
    """Everyone whose manifest entry for this item can change: owner, plus anyone holding a
    share on the item (files) or on the item or an ancestor folder."""
    folder = item.folder if isinstance(item, DriveFile) else item
    ids = []
    n = 0
    while folder is not None and n <= policy.MAX_DEPTH + 1:
        ids.append(folder.pk)
        folder = folder.parent
        n += 1
    q = Share.objects.filter(folder_id__in=ids)
    if isinstance(item, DriveFile):
        q = Share.objects.filter(file=item) | q
    return {item.owner_id} | set(q.values_list("user_id", flat=True))


def _nested(item):
    """Shares that live INSIDE a folder's subtree: (person id, root item). Their entries change
    when the folder is trashed, restored or moved. Bounded by the number of shares, not by the
    number of files."""
    if not isinstance(item, Folder):
        return []
    fids = [f.pk for f in _descendants(item)]
    out = []
    for sh in Share.objects.filter(folder_id__in=fids).exclude(folder_id=item.pk):
        out.append((sh.user_id, sh.folder))
    for sh in Share.objects.filter(file__folder_id__in=fids).select_related("file"):
        out.append((sh.user_id, sh.file))
    return out


def _is_lock_contention(exc):
    code = exc.args[0] if exc.args else None
    msg = str(exc.args[1]) if len(exc.args) > 1 else ""
    if code in (1205, 1213):
        return True
    return code == 1105 and "pessimistic lock retry limit" in msg.lower()


class LockSetGrew(Exception):
    """The set of people grew between reading it and locking it. The whole transaction is
    rolled back (releasing every lock) and re-run, so locks are never taken out of order."""


def _relock(fn):
    """Outermost wrapper: re-run the whole operation in a fresh transaction when the lock set
    grew. Never retries inside a transaction that already holds locks."""
    import functools

    from django.db import OperationalError

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from django.db import connection

        if connection.in_atomic_block:
            # A caller already owns a transaction (ATOMIC_REQUESTS is off in this project, so
            # this is a nested call or a test wrapper). Retrying here would re-run work inside
            # a transaction that may already be dead or hold locks in the wrong order, so run
            # once and let the caller see the error.
            try:
                with transaction.atomic():
                    return fn(*args, **kwargs)
            except LockSetGrew:
                # the enclosing transaction holds locks taken in the wrong order: force it to
                # roll back instead of letting a caller catch this and carry on
                transaction.set_rollback(True)
                raise ValidationError("Access changed while this ran. Try again.") from None
        last = None
        for attempt in range(5):
            body_done = False
            try:
                with transaction.atomic():
                    result = fn(*args, **kwargs)
                    body_done = True  # from here on, any error is raised by COMMIT itself
                return result
            except LockSetGrew as exc:
                last = exc
                continue
            except OperationalError as exc:
                # Retry only lock contention raised by a statement in the body. An error after
                # the body finished came from COMMIT: its outcome may be unknown, so it is
                # never replayed (phase flag, not text matching).
                if body_done:
                    logger.error("drive operation: error raised at commit, not replayed: %r", exc)
                    raise ValidationError(
                        "Could not confirm this change was saved. Check the item, then try again."
                    ) from exc
                if not _is_lock_contention(exc):
                    raise
                last = exc
                time.sleep(random.uniform(0.02, 0.1) * (attempt + 1))  # noqa: S311
        logger.error("drive operation gave up after retries: %r", last)
        raise ValidationError("Busy right now. Try again.")

    return wrapper


def _affected(item, extra_ids=(), also=()):
    want = _people(item) | set(extra_ids) | {p for p, _ in _nested(item)}
    for other in also:
        want |= _people(other)
    return want


def _lock_affected(item, *extra_ids, also=()):
    """Read the affected set, lock owner and all of them together in ascending id, re-read.
    If the set grew, raise LockSetGrew so the caller's transaction is rolled back and retried
    from scratch (see _relock)."""
    want = _affected(item, extra_ids, also)
    _lock_users(*want)
    _fresh(item)
    if not _affected(item, extra_ids, also) <= want:
        raise LockSetGrew()
    return want


def _bump(person_id, item, reason, people_state=None):
    """Write one scoped row for one person. Caller holds that person's account lock."""
    person = get_user_model().objects.get(pk=person_id)
    entry = mf.effective_entry(person, item)
    state = {
        mf.ACTIVE: ManifestChange.State.ACTIVE,
        mf.TRASHED: ManifestChange.State.TRASHED,
        mf.NONE: ManifestChange.State.REVOKED,
    }[entry.state]
    ep, _ = ManifestEpoch.objects.get_or_create(user_id=person_id)
    ep.epoch += 1
    ep.save(update_fields=["epoch", "updated_at"])
    is_folder = isinstance(item, Folder)
    ManifestChange.objects.create(
        user_id=person_id,
        epoch=ep.epoch,
        item_type="folder" if is_folder else "file",
        item_public_id=item.public_id,
        scope=ManifestChange.Scope.SUBTREE if is_folder else ManifestChange.Scope.ITEM,
        state=state,
        role=entry.role or "",
        reason=reason,
    )
    return ep.epoch


def _record(item, reason, people, nested=()):
    for pid in sorted(people):
        _bump(pid, item, reason)
    for pid, root in sorted(nested, key=lambda t: (t[0], str(t[1].public_id))):
        _bump(pid, root, reason)


MANIFEST_PAGE_EPOCHS = 200


def manifest_since(user, since=0, limit=None):
    """One page of changes in (since, next_since]. The epoch is read FIRST and the page is cut
    by epoch number (epochs are gap free per person), so a page is never split mid-epoch.
    Rows carry absolute state, so replay over a newer listing converges. A cursor ahead of the
    server (since > epoch) or no epoch row means the client must relist. Inactive accounts get
    nothing. Authorization is not taken from this: every list and transfer re-checks live."""
    if not user.is_active or getattr(user, "status", "active") != "active":
        raise PermissionDenied("Inactive account.")
    ep = ManifestEpoch.objects.filter(user=user).first()
    top = ep.epoch if ep else 0
    if ep is None or since > top or since < 0:
        return {
            "epoch": top,
            "next_since": 0,
            "has_more": False,
            "full_list_required": True,
            "changes": [],
        }
    last = min(top, since + min(max(1, int(limit or MANIFEST_PAGE_EPOCHS)), MANIFEST_PAGE_EPOCHS))
    rows = ManifestChange.objects.filter(user=user, epoch__gt=since, epoch__lte=last).order_by(
        "epoch", "id"
    )
    return {
        "epoch": top,
        "next_since": last,
        "has_more": last < top,
        "full_list_required": False,
        "changes": list(rows),
    }


def reconcile_user(user, fix=False):  # noqa: C901 (one linear scan then one locked fix pass)
    """Compare the last recorded state for each root with live truth. Without fix this only
    flags candidates. With fix it takes the ordered locks, recomputes fresh under them, and
    only then writes a correction, so a stale read can never overwrite current truth."""
    latest = {}
    for row in ManifestChange.objects.filter(user=user).order_by("epoch"):
        latest[(row.item_type, row.item_public_id)] = row
    # also every root the person holds a share on right now, so a grant that never got a row
    # (for example written directly in the database) is found, not only roots already recorded
    for sh in Share.objects.filter(user=user).select_related("folder", "file"):
        root = sh.folder or sh.file
        key = ("folder" if sh.folder_id else "file", root.public_id)
        latest.setdefault(key, None)
    flagged = []
    for (kind, pid), row in latest.items():
        model = Folder if kind == "folder" else DriveFile
        item = model.objects.filter(public_id=pid).first()
        if item is None:
            continue
        live = mf.effective_entry(user, item)
        want = {mf.ACTIVE: "active", mf.TRASHED: "trashed", mf.NONE: "revoked"}[live.state]
        if row is None:
            if want != "revoked":
                flagged.append((kind, pid))  # live access with no manifest row at all
        elif want != row.state or (live.role or "") != row.role:
            flagged.append((kind, pid))
    if not fix:
        return flagged
    corrected = []
    for kind, pid in flagged:
        model = Folder if kind == "folder" else DriveFile
        with transaction.atomic():
            item = model.objects.filter(public_id=pid).first()
            if item is None:
                continue
            try:
                _lock_affected(item, user.pk)
            except LockSetGrew:
                continue  # state moved under us; the next reconcile pass will see it
            last = (
                ManifestChange.objects.filter(user=user, item_public_id=pid)
                .order_by("-epoch")
                .first()
            )
            live = mf.effective_entry(user, item)
            want = {mf.ACTIVE: "active", mf.TRASHED: "trashed", mf.NONE: "revoked"}[live.state]
            if (last is None and want != "revoked") or (
                last is not None and (want != last.state or (live.role or "") != last.role)
            ):
                _bump(user.pk, item, "reconcile")
                audit(
                    None, "drive.manifest.reconcile", kind, pid, source="reconcile", person=user.pk
                )
                corrected.append((kind, pid))
                logger.warning(
                    "drive manifest reconcile corrected %s %s for user %s", kind, pid, user.pk
                )
    return corrected


@transaction.atomic
def create_file(user, name, folder=None, content_type=""):
    """Create a file RECORD (no bytes yet, current_version 0). Bytes arrive through add_version."""
    name = _clean_name(name)
    if folder is not None:
        _lock_tree(folder.owner_id)
        _fresh(folder)
        require(user, "edit", FOLDER, folder)
        if _in_trash(folder):
            raise ValidationError("Cannot add a file in the trash.")
    else:
        _lock_tree(user.pk)
    owner_id = folder.owner_id if folder is not None else user.pk
    if _name_taken(folder, name, owner_id):
        raise ValidationError("That name is already used here.")
    f = DriveFile.objects.create(
        owner_id=owner_id,
        folder=folder,
        name=name,
        content_type=(content_type or "")[:127],
        created_by=user,
    )
    _journal(user, f, "create")
    audit(user, "drive.file.create", FILE, f.pk, folder=getattr(folder, "pk", None))
    return f


class VersionConflict(ValidationError):
    """base_version is not the file's current version: someone else saved first."""


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@transaction.atomic
def add_version(user, file, base_version, size, sha256):
    """Record a new version only if the caller saw the current one (compare and set under the
    owner lock). The server mints the opaque storage key (unique across all files); callers
    never choose it. Nothing here writes or reads bytes. No maximum size is enforced because the
    product file-size limit has not been chosen."""
    _lock_tree(file.owner_id)
    _fresh(file)
    require(user, "edit", FILE, file)
    if file.trashed_at is not None or (file.folder_id and _in_trash(file.folder)):
        raise ValidationError("Restore the file before saving a new version.")
    if not _HEX64.match(sha256 or ""):
        raise ValidationError("Invalid checksum.")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValidationError("Invalid size.")
    if base_version != file.current_version:
        raise VersionConflict(
            f"This file changed (now version {file.current_version}); you saved from {base_version}."
        )
    number = file.current_version + 1
    v = FileVersion.objects.create(
        file=file,
        number=number,
        storage_key=secrets.token_hex(32),
        size=size,
        sha256=sha256,
        uploaded_by=user,
    )
    DriveFile.objects.filter(pk=file.pk).update(current_version=number, size=size)
    _journal(user, file, "version")
    audit(user, "drive.file.version", FILE, file.pk, number=number, size=size)
    file.refresh_from_db()
    return v


def _kind(item):
    return FOLDER if isinstance(item, Folder) else FILE


@transaction.atomic
def rename(user, item, name):
    _lock_tree(item.owner_id)
    _fresh(item)
    kind = _kind(item)
    require(user, "rename", kind, item)
    name = _clean_name(name)
    parent = item.parent if isinstance(item, Folder) else item.folder
    if _name_taken(parent, name, item.owner_id, exclude=item):
        raise ValidationError("That name is already used here.")
    item.name = name
    item.save(update_fields=["name"])
    _journal(user, item, "rename")
    audit(user, "drive.rename", kind, item.pk)
    return item


def _height(folder):
    """Levels in the subtree under (and including) this folder."""
    h, frontier = 0, [folder.pk]
    while frontier:
        h += 1
        frontier = list(Folder.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
    return h


def _check_destination(user, item, destination):
    if destination is None:
        if item.owner_id != user.pk:
            raise PermissionDenied("Only the owner can move an item to the top level.")
        return
    _fresh(destination)
    require(user, "edit", FOLDER, destination)
    if _in_trash(destination):
        raise ValidationError("Cannot move into the trash.")
    if destination.owner_id != item.owner_id:
        raise ValidationError("Items stay inside their owner's drive.")
    if not isinstance(item, Folder):
        return
    node = destination
    while node is not None:
        if node.pk == item.pk:
            raise ValidationError("A folder cannot be moved into itself.")
        node = node.parent
    if _depth(destination) + _height(item) > policy.MAX_DEPTH:
        raise ValidationError("That would nest folders too deeply.")


def _validate_move(user, item, destination, kind):
    """The one set of move rules. move() runs it under its locks; check_move() runs it for the page."""
    require(user, "move", kind, item)
    _check_destination(user, item, destination)
    if _name_taken(destination, item.name, item.owner_id, exclude=item):
        raise ValidationError("That name is already used in the destination.")


@_relock
def move(user, item, destination):
    """Move to another folder (None = the owner's top level). Folders cannot enter themselves."""
    also = (destination,) if destination is not None else ()
    _lock_affected(item, also=also)
    people = _people(item)
    nested = _nested(item)
    kind = _kind(item)
    _validate_move(user, item, destination, kind)
    if isinstance(item, Folder):
        item.parent = destination
        item.save(update_fields=["parent"])
    else:
        item.folder = destination
        item.save(update_fields=["folder"])
    _journal(user, item, "move")
    audit(user, "drive.move", kind, item.pk, to=getattr(destination, "pk", None))
    item.refresh_from_db()
    _record(item, "move", people | _people(item), nested)
    return item


def check_move(user, item, destination):
    """The move rules without changing anything: raises exactly what move() would raise for permission,
    destination, nesting and name problems. move() still re-checks everything under its locks."""
    kind = _kind(item)
    _validate_move(user, item, destination, kind)


def audience_digest(item, destination, gain):
    """A short fingerprint of what a confirmation showed: the item, the destination and each person with
    their new and old role. If the sharing changes before the second press, the fingerprint differs."""
    import hashlib

    parts = [str(item.pk), _kind(item), str(getattr(destination, "pk", "")), *(f"{u.pk}:{n}:{o or ''}" for u, n, o in gain)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


_ROLE_RANK = {"viewer": 1, "commenter": 2, "editor": 3}


def _chain_ids(folder):
    ids, n = [], 0
    while folder is not None and n <= policy.MAX_DEPTH + 1:
        ids.append(folder.pk)
        folder = folder.parent
        n += 1
    return ids


def _best_roles(item, parent_folder):
    """{person id: strongest role} from shares on the item itself and on the folders above it
    when it sits in `parent_folder`. Read-only; the same inheritance rule as policy.role_for."""
    q = Share.objects.filter(folder_id__in=_chain_ids(parent_folder))
    if isinstance(item, DriveFile):
        q = Share.objects.filter(file=item) | q
    else:
        q = Share.objects.filter(folder=item) | q
    out = {}
    for uid, role in q.values_list("user_id", "role"):
        if _ROLE_RANK.get(role, 0) > _ROLE_RANK.get(out.get(uid), 0):
            out[uid] = role
    return out


def nested_share_count(item):
    """How many live items inside a folder have a share set directly on them (0 for a file); things in the trash
    are left out, because they are not part of the folder people see. Read-only. A move does not change
    them. Only the owner is shown this number; anyone else gets a generic sentence."""
    if not isinstance(item, Folder):
        return 0
    live = [f.pk for f in _descendants(item) if f.trashed_at is None]
    folders = Share.objects.filter(folder_id__in=live).exclude(folder_id=item.pk).values("folder_id").distinct().count()
    files = (
        Share.objects.filter(file__folder_id__in=live, file__trashed_at__isnull=True).values("file_id").distinct().count()
    )
    return folders + files  # distinct items, not share rows: one item shared with two people counts once


def move_audience(item, destination):
    """Who would gain or lose access to `item` if it moved into `destination` (None = top level).
    Returns (gain, lose): gain is [(user, new role, old role or None)] for people who get access or a
    stronger role; lose is [(user, old role, new role or None)] for people who lose it or get a weaker
    role. Shares set directly on the item stay. Nothing is changed."""
    current = item.folder if isinstance(item, DriveFile) else item.parent
    before, after = _best_roles(item, current), _best_roles(item, destination)
    ids = (set(before) | set(after)) - {item.owner_id}
    users = {u.pk: u for u in get_user_model().objects.filter(pk__in=ids)}
    gain, lose = [], []
    for uid in sorted(ids, key=lambda i: (users[i].employee_id if i in users else "")):
        if uid not in users:
            continue
        b, a = before.get(uid), after.get(uid)
        if a and _ROLE_RANK[a] > _ROLE_RANK.get(b, 0):
            gain.append((users[uid], a, b))
        elif b and _ROLE_RANK[b] > _ROLE_RANK.get(a, 0):
            lose.append((users[uid], b, a))
    return gain, lose


def _descendants(folder):
    folders, frontier = [folder], [folder.pk]
    while frontier:
        kids = list(Folder.objects.filter(parent_id__in=frontier))
        folders.extend(kids)
        frontier = [k.pk for k in kids]
    return folders


@_relock
def trash(user, item):
    """Move to trash as one batch (a folder takes its contents with it). Nothing is deleted."""
    _lock_affected(item)
    people = _people(item)
    nested = _nested(item)
    kind = _kind(item)
    require(user, "trash", kind, item)
    now, batch = timezone.now(), uuid.uuid4()
    mark = {"trashed_at": now, "trashed_by": user, "trash_batch": batch}
    if isinstance(item, Folder):
        ids = [f.pk for f in _descendants(item)]
        Folder.objects.filter(pk__in=ids, trashed_at__isnull=True).update(**mark)
        DriveFile.objects.filter(folder_id__in=ids, trashed_at__isnull=True).update(**mark)
    else:
        DriveFile.objects.filter(pk=item.pk, trashed_at__isnull=True).update(**mark)
    _journal(user, item, "trash")
    audit(user, "drive.trash", kind, item.pk)
    item.refresh_from_db()
    _record(item, "trash", people, nested)
    return item


@_relock
def restore(user, item):
    """Bring back one trash batch. It returns to its old place; if that place is gone, is in
    the trash, would collide by name, or would nest too deep, nothing changes and the
    person is told. Only the owner may re-home a batch at the top level."""
    _lock_affected(item)
    people = _people(item)
    nested = _nested(item)
    kind = _kind(item)
    require(user, "restore", kind, item)
    if item.trash_batch is None:
        raise ValidationError("This item is not in the trash.")
    parent = item.parent if isinstance(item, Folder) else item.folder
    if parent is not None and _in_trash(parent):
        if user.pk != item.owner_id:
            raise ValidationError("The folder it was in is still in the trash.")
        parent = None  # owner only: come back at the top level
    if _name_taken(parent, item.name, item.owner_id, exclude=item):
        raise ValidationError("An item with that name already exists there. Rename one first.")
    if isinstance(item, Folder):
        if parent is not None and _depth(parent) + _height(item) > policy.MAX_DEPTH:
            raise ValidationError("That would nest folders too deeply.")
        ids = [f.pk for f in _descendants(item)]
        batch = item.trash_batch
        if parent != item.parent:
            item.parent = parent
            item.save(update_fields=["parent"])
        clear = {"trashed_at": None, "trashed_by": None, "trash_batch": None}
        Folder.objects.filter(pk__in=ids, trash_batch=batch).update(**clear)
        DriveFile.objects.filter(folder_id__in=ids, trash_batch=batch).update(**clear)
    else:
        if parent != item.folder:
            item.folder = parent
            item.save(update_fields=["folder"])
        DriveFile.objects.filter(pk=item.pk).update(
            trashed_at=None, trashed_by=None, trash_batch=None
        )
    _journal(user, item, "restore")
    audit(user, "drive.restore", kind, item.pk)
    item.refresh_from_db()
    # recomputed from live access now, not replayed from the shares that existed before
    people = people | _people(item)
    _record(item, "restore", people, nested)
    return item


@_relock
def share(user, item, person, role):
    _lock_users(item.owner_id, person.pk)
    _fresh(item)
    kind = _kind(item)
    require(user, "share", kind, item)
    if role not in (Share.Role.VIEWER, Share.Role.EDITOR):
        raise ValidationError("Share as viewer or editor.")  # commenting is not built yet
    if item.trashed_at is not None:
        raise ValidationError("Restore it from the trash before sharing.")
    person = type(person).objects.get(pk=person.pk)
    if person.pk == item.owner_id:
        raise ValidationError("The owner already has full access.")
    if not person.is_active or getattr(person, "status", "active") != "active":
        raise ValidationError("That person's account is not active.")
    target = {"folder": item} if isinstance(item, Folder) else {"file": item}
    obj, created = Share.objects.update_or_create(
        user=person, **target, defaults={"role": role, "granted_by": user}
    )
    _journal(user, item, "share", subject=person)
    audit(user, "drive.share", kind, item.pk, person=person.pk, role=role)
    _bump(person.pk, item, "share")
    if created:
        # Title carries the sharer's name only, never the item name; the link is the opaque id.
        # A notification problem must never undo the share, so it runs in its own savepoint.
        link = f"/drive/folder/{item.public_id}/" if isinstance(item, Folder) else "/drive/shared/"
        label = "folder" if isinstance(item, Folder) else "file"
        try:
            with transaction.atomic():
                notify(
                    person,
                    "drive.share",
                    f"{user.full_name or user.employee_id} shared a {label} with you",
                    link,
                    check=(kind, item),
                )
        except DatabaseError:
            # a database error (deadlock, lock timeout) may already have rolled the server
            # transaction back: never continue past it, let the outer rollback and retry run
            raise
        except Exception:  # noqa: BLE001
            # any other notice failure never undoes the share
            logger.exception("drive share notification skipped")
    return obj


@_relock
def unshare(user, item, person):
    _lock_users(item.owner_id, person.pk)
    _fresh(item)
    kind = _kind(item)
    require(user, "unshare", kind, item)
    target = {"folder": item} if isinstance(item, Folder) else {"file": item}
    n, _ = Share.objects.filter(user=person, **target).delete()
    if n:
        _journal(user, item, "unshare", subject=person)
        audit(user, "drive.unshare", kind, item.pk, person=person.pk)
        _bump(person.pk, item, "unshare")
        # An independent grant inside the subtree stays valid. Say so explicitly so a device
        # that drops the revoked root keeps those items (it re-lists with live checks anyway).
        for pid, root in _nested(item):
            if pid == person.pk:
                _bump(person.pk, root, "unshare_keep")
        return n
    if policy.role_for(person, item) is not None and person.pk != item.owner_id:
        raise ValidationError(
            "This person's access comes from a shared parent folder. Remove it there."
        )
    return 0


def list_folder(user, folder=None):
    """Children of a folder the user may view (top level = the user's own items)."""
    if folder is None:
        return (
            list(Folder.objects.filter(owner=user, parent=None, trashed_at__isnull=True)),
            list(DriveFile.objects.filter(owner=user, folder=None, trashed_at__isnull=True)),
        )
    require(user, "view", FOLDER, folder)
    return (
        list(Folder.objects.filter(parent=folder, trashed_at__isnull=True).select_related("created_by")),
        list(DriveFile.objects.filter(folder=folder, trashed_at__isnull=True).select_related("created_by")),
    )


def shared_with_me(user):
    """Items someone shared directly with the user (not inherited), excluding the trash."""
    shares = Share.objects.filter(user=user).select_related("folder", "file")
    out = []
    for s in shares:
        item = s.folder or s.file
        if (
            item is not None
            and item.trashed_at is None
            and can(user, "view", FOLDER if s.folder else FILE, item)
        ):
            out.append((item, s.role))
    return out


def trash_items(user):
    return (
        list(Folder.objects.filter(owner=user, trashed_at__isnull=False)),
        list(DriveFile.objects.filter(owner=user, trashed_at__isnull=False)),
    )


MAX_SEARCH_DEPTH = 20


def _reachable_folder_ids(user):
    """Folder ids the user can reach through a direct share on a folder, descendants included."""
    frontier = list(
        Share.objects.filter(
            user=user, folder__isnull=False, folder__trashed_at__isnull=True
        ).values_list("folder_id", flat=True)
    )
    seen = set(frontier)
    for _ in range(MAX_SEARCH_DEPTH):
        if not frontier:
            break
        frontier = [
            fid
            for fid in Folder.objects.filter(
                parent_id__in=frontier, trashed_at__isnull=True
            ).values_list("pk", flat=True)
            if fid not in seen
        ]
        seen.update(frontier)
    return seen


def search_items(user, query, limit):
    """Searcher for the foundation hook: owned plus shared items only, in the query itself.

    can() runs again in the hook as a backstop (for example for an ancestor that was trashed).
    """
    from django.db.models import Q
    from django.db.models.functions import Upper

    needle = query.upper()  # icontains is case-sensitive on TiDB's binary collation

    reach = _reachable_folder_ids(user)
    folders = (
        Folder.objects.annotate(_n=Upper("name"))
        .filter(trashed_at__isnull=True, _n__contains=needle)
        .filter(Q(owner=user) | Q(pk__in=reach))
        .order_by("name")[:limit]
    )
    files = (
        DriveFile.objects.annotate(_n=Upper("name"))
        .filter(trashed_at__isnull=True, _n__contains=needle)
        .filter(Q(owner=user) | Q(folder_id__in=reach) | Q(shares__user=user))
        .distinct()
        .order_by("name")[:limit]
    )
    for f in folders:
        yield (FOLDER, f, f.name, f"/drive/folder/{f.public_id}/")
    for f in files:
        # there is no file detail page yet, so a file hit opens the folder that holds it
        if f.folder_id is None:
            url = "/drive/"
        elif can(user, "view", FOLDER, f.folder):
            url = f"/drive/folder/{f.folder.public_id}/"
        else:
            url = "/drive/shared/"  # never reveal the id of a folder they cannot open
        yield (FILE, f, f.name, url)
