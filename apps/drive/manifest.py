"""Access manifest rules (first piece: the single decision function).

This module decides, for one person and one item, whether the item is eligible for active
sync, only visible as trash, or not reachable at all. Tables, epochs and rows are designed in
salow-drive-manifest-design.md and are NOT built. Nothing here touches file bytes.
"""

from dataclasses import dataclass
from typing import Optional

from apps.core.foundation.permissions import can
from apps.drive import policy
from apps.drive.models import DriveFile, Folder

ACTIVE = "active"  # can view, nothing in the chain is trashed: eligible for sync
TRASHED = "trashed"  # can view (owner or the person who trashed it) but it is in the trash
NONE = "none"  # no view right, or the person is not an active account


@dataclass(frozen=True)
class Entry:
    state: str
    role: Optional[str]


def _chain_trashed(item) -> bool:
    folder = item.folder if isinstance(item, DriveFile) else item
    n = 0
    while folder is not None and n <= policy.MAX_DEPTH + 1:
        if folder.trashed_at is not None:
            return True
        n += 1
        folder = folder.parent
    return False


def effective_entry(user, item) -> Entry:
    """The only place that maps (person, item) to a manifest state.

    can(view) alone is not enough: the owner and the trasher may view a trashed item, but a
    device must not treat it as active content.
    """
    kind = "drive.file" if isinstance(item, DriveFile) else "drive.folder"
    if not isinstance(item, (DriveFile, Folder)) or not can(user, "view", kind, item):
        return Entry(NONE, None)
    role = policy.role_for(user, item)
    if item.trashed_at is not None:
        return Entry(TRASHED, role)
    if _chain_trashed(item):
        # Own flag clear but an ancestor is in the trash: only the owner may still see it, as
        # trash. Anyone else (a sharee) has lost it until the ancestor is restored.
        return Entry(TRASHED, role) if role == "owner" else Entry(NONE, None)
    return Entry(ACTIVE, role)
