"""Drive operations. Every function checks the foundation permission policy first."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.foundation.audit import audit
from apps.core.foundation.permissions import can, require
from apps.drive import policy  # noqa: F401  (registers the policies)
from apps.drive.models import DriveChange, DriveFile, Folder, Share

FOLDER, FILE = "drive.folder", "drive.file"


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


def _journal(user, item, action):
    kind = "folder" if isinstance(item, Folder) else "file"
    DriveChange.objects.create(
        owner_id=item.owner_id, item_type=kind, item_id=item.pk, action=action, actor=user
    )


def _lock_namespace(parent, owner_id):
    """Serialize name checks in one folder (or one top level) so two writers cannot both pass."""
    if parent is not None:
        Folder.objects.select_for_update().filter(pk=parent.pk).first()
    else:
        get_user_model().objects.select_for_update().filter(pk=owner_id).first()


def _depth(folder):
    n = 0
    while folder is not None and n <= policy.MAX_DEPTH + 1:
        n += 1
        folder = folder.parent
    return n


def _name_taken(parent, name, owner_id, exclude=None):
    _lock_namespace(parent, owner_id)
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
        require(user, "edit", FOLDER, parent)
    # Build default, not user policy: an item created inside a shared folder belongs to the
    # folder's owner; the creator is recorded separately.
    owner_id = parent.owner_id if parent is not None else user.pk
    if parent is not None and (parent.trashed_at is not None or _depth(parent) >= policy.MAX_DEPTH):
        raise ValidationError("Cannot create a folder here (in the trash or nested too deep).")
    if _name_taken(parent, name, owner_id):
        raise ValidationError("That name is already used here.")
    folder = Folder.objects.create(owner_id=owner_id, parent=parent, name=name, created_by=user)
    _journal(user, folder, "create")
    audit(user, "drive.folder.create", FOLDER, folder.pk, parent=getattr(parent, "pk", None))
    return folder


@transaction.atomic
def rename(user, item, name):
    kind = FOLDER if isinstance(item, Folder) else FILE
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
    require(user, "edit", FOLDER, destination)
    if destination.trashed_at is not None:
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
    kind = FOLDER if isinstance(item, Folder) else FILE
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
    """Move to trash as one unit (a folder takes its contents with it). Nothing is deleted."""
    kind = FOLDER if isinstance(item, Folder) else FILE
    require(user, "trash", kind, item)
    now = timezone.now()
    if isinstance(item, Folder):
        folders = _descendants(item)
        ids = [f.pk for f in folders]
        Folder.objects.filter(pk__in=ids, trashed_at__isnull=True).update(
            trashed_at=now, trashed_by=user
        )
        DriveFile.objects.filter(folder_id__in=ids, trashed_at__isnull=True).update(
            trashed_at=now, trashed_by=user
        )
    else:
        DriveFile.objects.filter(pk=item.pk).update(trashed_at=now, trashed_by=user)
    _journal(user, item, "trash")
    audit(user, "drive.trash", kind, item.pk)
    item.refresh_from_db()
    return item


@transaction.atomic
def restore(user, item):
    kind = FOLDER if isinstance(item, Folder) else FILE
    require(user, "restore", kind, item)
    stamp = item.trashed_at
    if isinstance(item, Folder):
        parent = item.parent
        ids = [f.pk for f in _descendants(item)]
        if parent is not None and parent.trashed_at is not None:
            item.parent = None  # parent is still in the trash: come back at the top level
            item.save(update_fields=["parent"])
        Folder.objects.filter(pk__in=ids, trashed_at=stamp).update(trashed_at=None, trashed_by=None)
        DriveFile.objects.filter(folder_id__in=ids, trashed_at=stamp).update(
            trashed_at=None, trashed_by=None
        )
    else:
        if item.folder is not None and item.folder.trashed_at is not None:
            item.folder = None
            item.save(update_fields=["folder"])
        DriveFile.objects.filter(pk=item.pk).update(trashed_at=None, trashed_by=None)
    _journal(user, item, "restore")
    audit(user, "drive.restore", kind, item.pk)
    item.refresh_from_db()
    return item


@transaction.atomic
def share(user, item, person, role):
    kind = FOLDER if isinstance(item, Folder) else FILE
    require(user, "share", kind, item)
    if role not in (Share.Role.VIEWER, Share.Role.EDITOR, Share.Role.COMMENTER):
        raise ValidationError("Unknown role.")
    if person.pk == item.owner_id:
        raise ValidationError("The owner already has full access.")
    if role == Share.Role.COMMENTER:
        # Commenting is not built yet, so the role is not offered until it works.
        raise ValidationError("Share as viewer or editor.")
    if not person.is_active or getattr(person, "status", "active") != "active":
        raise ValidationError("That person's account is not active.")
    target = {"folder": item} if isinstance(item, Folder) else {"file": item}
    obj, _ = Share.objects.update_or_create(
        user=person, **target, defaults={"role": role, "granted_by": user}
    )
    _journal(user, item, "share")
    audit(user, "drive.share", kind, item.pk, person=person.pk, role=role)
    return obj


@transaction.atomic
def unshare(user, item, person):
    kind = FOLDER if isinstance(item, Folder) else FILE
    require(user, "unshare", kind, item)
    target = {"folder": item} if isinstance(item, Folder) else {"file": item}
    n, _ = Share.objects.filter(user=person, **target).delete()
    if n:
        _journal(user, item, "unshare")
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
