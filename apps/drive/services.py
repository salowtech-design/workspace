"""Drive operations. Every function checks the foundation permission policy first."""

import logging
import re
import secrets
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.foundation.audit import audit
from apps.core.foundation.notify import notify
from apps.core.foundation.permissions import can, require
from apps.drive import policy  # noqa: F401  (registers the policies)
from apps.drive.models import DriveChange, DriveFile, FileVersion, Folder, Share

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


@transaction.atomic
def move(user, item, destination):
    """Move to another folder (None = the owner's top level). Folders cannot enter themselves."""
    _lock_tree(item.owner_id)
    _fresh(item)
    kind = _kind(item)
    require(user, "move", kind, item)
    _check_destination(user, item, destination)
    if _name_taken(destination, item.name, item.owner_id, exclude=item):
        raise ValidationError("That name is already used in the destination.")
    if isinstance(item, Folder):
        item.parent = destination
        item.save(update_fields=["parent"])
    else:
        item.folder = destination
        item.save(update_fields=["folder"])
    _journal(user, item, "move")
    audit(user, "drive.move", kind, item.pk, to=getattr(destination, "pk", None))
    return item


def _descendants(folder):
    folders, frontier = [folder], [folder.pk]
    while frontier:
        kids = list(Folder.objects.filter(parent_id__in=frontier))
        folders.extend(kids)
        frontier = [k.pk for k in kids]
    return folders


@transaction.atomic
def trash(user, item):
    """Move to trash as one batch (a folder takes its contents with it). Nothing is deleted."""
    _lock_tree(item.owner_id)
    _fresh(item)
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
    return item


@transaction.atomic
def restore(user, item):
    """Bring back one trash batch. It returns to its old place; if that place is gone, is in
    the trash, would collide by name, or would nest too deep, nothing changes and the
    person is told. Only the owner may re-home a batch at the top level."""
    _lock_tree(item.owner_id)
    _fresh(item)
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
    return item


@transaction.atomic
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
        except Exception:  # noqa: BLE001
            logger.exception("drive share notification skipped")
    return obj


@transaction.atomic
def unshare(user, item, person):
    _lock_tree(item.owner_id)
    _fresh(item)
    kind = _kind(item)
    require(user, "unshare", kind, item)
    target = {"folder": item} if isinstance(item, Folder) else {"file": item}
    n, _ = Share.objects.filter(user=person, **target).delete()
    if n:
        _journal(user, item, "unshare", subject=person)
        audit(user, "drive.unshare", kind, item.pk, person=person.pk)
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
        list(Folder.objects.filter(parent=folder, trashed_at__isnull=True)),
        list(DriveFile.objects.filter(folder=folder, trashed_at__isnull=True)),
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
