"""Add-ons step 2: the version state machine and a deny-all authority gate.

Transitions follow the blueprint figure (draft -submit-> review -approve-> approved; review -reject-> draft; approved
-suspend-> suspended -restore-> approved) and describe AddonVersion.review_state. How Addon.status follows the version states is
undecided and not built. Who may perform each step is a user-owned decision that is NOT made: perform() calls require_authority
first and require_authority refuses every caller, so nothing in this module changes a row. _next_state is pure and is NOT an
authorisation check; it only says whether a move exists on the figure."""

from django.core.exceptions import PermissionDenied

from apps.addons.models import Addon

TRANSITIONS = {
    ("submit", Addon.DRAFT): Addon.REVIEW,
    ("approve", Addon.REVIEW): Addon.APPROVED,
    ("reject", Addon.REVIEW): Addon.DRAFT,
    ("suspend", Addon.APPROVED): Addon.SUSPENDED,
    ("restore", Addon.SUSPENDED): Addon.APPROVED,
}
ACTIONS = ("submit", "approve", "reject", "suspend", "restore")


def _next_state(action: str, state: str) -> str:
    """Pure, NOT authorisation, touches no database: the new state, or ValueError when the figure has no such move."""
    try:
        return TRANSITIONS[(action, state)]
    except (KeyError, TypeError):
        raise ValueError(f"Cannot {action!r} an add-on version that is {state!r}.") from None


def require_authority(actor, action: str) -> None:
    """Deny everyone until the owner decides who may submit, approve, suspend or install. Not a placeholder grant."""
    raise PermissionDenied("Add-on actions are not enabled: who may perform them has not been decided.")


def perform(actor, action: str, version):
    """The only entry point. Authority is checked first and always refuses today, so no state is read or changed."""
    require_authority(actor, action)
    raise AssertionError("unreachable until authority is decided")  # pragma: no cover
