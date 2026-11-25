"""Object-level permission check. Default deny.

A module registers one policy per resource type. A policy is a function
(user, action, obj) -> bool. Anything unregistered, anonymous or inactive is refused.
"""

from typing import Any, Callable, Optional

Policy = Callable[[Any, str, Any], bool]
_POLICIES: dict[str, Policy] = {}


def register_policy(resource_type: str, policy: Policy) -> None:
    if resource_type in _POLICIES and _POLICIES[resource_type] is not policy:
        raise ValueError(f"policy for {resource_type!r} already registered")
    _POLICIES[resource_type] = policy


def can(user: Optional[Any], action: str, resource_type: str, obj: Any) -> bool:
    """True only when the user is active, status is ACTIVE and the registered policy allows the action."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_active", False):
        return False
    # The rest of the app also requires an ACTIVE account status (suspended or locked
    # people keep is_active but must not pass any policy).
    if getattr(user, "status", "active") != "active":
        return False
    policy = _POLICIES.get(resource_type)
    if policy is None:
        return False
    try:
        return bool(policy(user, action, obj))
    except Exception:  # noqa: BLE001 - a policy bug must refuse, never allow
        import logging

        logging.getLogger(__name__).exception("policy for %s raised; refusing", resource_type)
        return False


def require(user: Optional[Any], action: str, resource_type: str, obj: Any) -> None:
    from django.core.exceptions import PermissionDenied

    if not can(user, action, resource_type, obj):
        raise PermissionDenied(f"{action} on {resource_type} is not allowed")


def registered_types() -> list[str]:
    return sorted(_POLICIES)
