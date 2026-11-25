"""Drive access: the owner, or a person with an explicit share on the item or an ancestor folder.

Strongest role wins. Moving an item under another folder changes what it inherits.
Admin roles get no automatic access to other people's files (policy choice not made).
"""

from apps.core.foundation.permissions import register_policy
from apps.drive.models import DriveFile, Share

RANK = {"viewer": 1, "commenter": 2, "editor": 3, "owner": 4}
NEEDS = {
    "view": 1,
    "download": 1,
    "comment": 2,
    "edit": 3,
    "rename": 3,
    "move": 3,
    "trash": 3,
    "restore": 3,
    "upload_version": 3,
    "share": 4,
    "unshare": 4,
    "delete_forever": 4,
    "transfer": 4,
}


MAX_DEPTH = 20  # folders nest at most this deep; ancestor lookups are bounded by it


def _chain(folder):
    seen = set()
    while folder is not None and folder.pk not in seen and len(seen) <= MAX_DEPTH + 1:
        seen.add(folder.pk)
        yield folder
        folder = folder.parent


def role_for(user, item):
    if item.owner_id == user.pk:
        return "owner"
    folder = item.folder if isinstance(item, DriveFile) else item
    ids = [f.pk for f in _chain(folder)]
    grants = Share.objects.filter(user=user)
    if isinstance(item, DriveFile):
        qs = grants.filter(file=item) | grants.filter(folder__in=ids)
    else:
        qs = grants.filter(folder__in=ids)
    best = 0
    for g in qs:
        # a folder share only reaches "editor" as "organizer"; commenter is file-level only
        best = max(best, RANK[g.role])
    return {v: k for k, v in RANK.items()}.get(best)


def _ancestor_trashed(item):
    folder = item.folder if isinstance(item, DriveFile) else item.parent
    n = 0
    while folder is not None and n <= MAX_DEPTH + 1:
        if folder.trashed_at is not None:
            return True
        folder = folder.parent
        n += 1
    return False


def _policy(user, action, item):
    need = NEEDS.get(action)
    if need is None:
        return False
    if item.trashed_at is None and _ancestor_trashed(item):
        # Own flag clear but a folder above is in the trash (normally trash marks every
        # descendant, so this is a defence). Only the owner may still look at it.
        return item.owner_id == user.pk and action == "view"
    if item.trashed_at is not None:
        # A trashed item is only reachable by its owner and by the person who trashed it,
        # and only to look at it or bring it back (owner may also delete it forever).
        if item.owner_id == user.pk:
            return action in ("view", "restore", "delete_forever")
        if item.trashed_by_id == user.pk and role_for(user, item) is not None:
            return action in ("view", "restore") and RANK[role_for(user, item)] >= need
        return False
    role = role_for(user, item)
    return role is not None and RANK[role] >= need


register_policy("drive.folder", _policy)
register_policy("drive.file", _policy)
