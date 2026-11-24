"""
Salow Workspace — Central Authorization Engine & Permission Registry.
Enforces default-deny access control, object-level rules, and role hierarchies.
"""

from functools import wraps
from typing import Callable, Optional

from django.core.exceptions import PermissionDenied

from apps.accounts.auth.stepup import (
    SUDO_GRANTED_TIMESTAMP_KEY as SUDO_SESSION_KEY,
)
from apps.accounts.auth.stepup import (
    grant_sudo,
    is_sudo_active,
    require_sudo,
)
from apps.accounts.models import CustomUser

# ------------------------------------------------------------------------------
# 1. PERMISSION CODE DEFINITIONS
# ------------------------------------------------------------------------------

PERM_USER_VIEW = "accounts.user.view"
PERM_USER_CREATE = "accounts.user.create"
PERM_USER_EDIT = "accounts.user.edit"
PERM_USER_DEACTIVATE = "accounts.user.deactivate"

PERM_TEAM_VIEW = "directory.team.view"
PERM_TEAM_MANAGE = "directory.team.manage"

PERM_ATTENDANCE_VIEW_SELF = "attendance.session.view_self"
PERM_ATTENDANCE_VIEW_TEAM = "attendance.session.view_team"
PERM_ATTENDANCE_VIEW_ALL = "attendance.session.view_all"
PERM_ATTENDANCE_APPROVE = "attendance.session.approve"
PERM_ATTENDANCE_OVERRIDE = "attendance.session.override"

PERM_POST_CREATE = "posts.post.create"
PERM_POST_VIEW_TEAM = "posts.post.view_team"
PERM_POST_VIEW_ALL = "posts.post.view_all"
PERM_POST_MODERATE = "posts.post.moderate"

PERM_CERTIFICATE_ISSUE = "certificates.cert.issue"
PERM_CERTIFICATE_REVOKE = "certificates.cert.revoke"

PERM_SETTINGS_MANAGE = "core.settings.manage"
PERM_AUDIT_VIEW = "core.audit.view"

PERM_RECOVERY_DELEGATED = "accounts.recovery.delegated"
PERM_RECOVERY_ADMIN = "accounts.recovery.admin"
PERM_BACKUP_MANAGE = "accounts.backup.manage"

PERM_SECURITY_EVENTS_READ = "security.events.read"
PERM_SECURITY_ALERTS_ACK = "security.alerts.acknowledge"
PERM_DEVICE_APPROVE = "accounts.device.approve"

# ------------------------------------------------------------------------------
# 2. ROLE TO PERMISSION MATRIX (DEFAULT-DENY)
# ------------------------------------------------------------------------------

ROLE_PERMISSIONS: dict[str, set[str]] = {
    CustomUser.Role.SUPER_ADMIN: {
        PERM_USER_VIEW,
        PERM_USER_CREATE,
        PERM_USER_EDIT,
        PERM_USER_DEACTIVATE,
        PERM_TEAM_VIEW,
        PERM_TEAM_MANAGE,
        PERM_ATTENDANCE_VIEW_SELF,
        PERM_ATTENDANCE_VIEW_TEAM,
        PERM_ATTENDANCE_VIEW_ALL,
        PERM_ATTENDANCE_APPROVE,
        PERM_ATTENDANCE_OVERRIDE,
        PERM_POST_CREATE,
        PERM_POST_VIEW_TEAM,
        PERM_POST_VIEW_ALL,
        PERM_POST_MODERATE,
        PERM_CERTIFICATE_ISSUE,
        PERM_CERTIFICATE_REVOKE,
        PERM_SETTINGS_MANAGE,
        PERM_AUDIT_VIEW,
        PERM_RECOVERY_DELEGATED,
        PERM_RECOVERY_ADMIN,
        PERM_BACKUP_MANAGE,
        PERM_SECURITY_EVENTS_READ,
        PERM_SECURITY_ALERTS_ACK,
        PERM_DEVICE_APPROVE,
    },
    CustomUser.Role.ADMIN: {
        PERM_USER_VIEW,
        PERM_USER_CREATE,
        PERM_USER_EDIT,
        PERM_USER_DEACTIVATE,
        PERM_TEAM_VIEW,
        PERM_TEAM_MANAGE,
        PERM_ATTENDANCE_VIEW_SELF,
        PERM_ATTENDANCE_VIEW_TEAM,
        PERM_ATTENDANCE_VIEW_ALL,
        PERM_ATTENDANCE_APPROVE,
        PERM_ATTENDANCE_OVERRIDE,
        PERM_POST_CREATE,
        PERM_POST_VIEW_TEAM,
        PERM_POST_VIEW_ALL,
        PERM_POST_MODERATE,
        PERM_CERTIFICATE_ISSUE,
        PERM_CERTIFICATE_REVOKE,
        PERM_SETTINGS_MANAGE,
        PERM_AUDIT_VIEW,
        PERM_RECOVERY_DELEGATED,
        PERM_RECOVERY_ADMIN,
        PERM_SECURITY_EVENTS_READ,
        PERM_SECURITY_ALERTS_ACK,
        PERM_DEVICE_APPROVE,
    },
    CustomUser.Role.SECURITY_ADMIN: {
        PERM_USER_VIEW,
        PERM_AUDIT_VIEW,
        PERM_SECURITY_EVENTS_READ,
        PERM_SECURITY_ALERTS_ACK,
        PERM_DEVICE_APPROVE,
    },
    CustomUser.Role.SECURITY_REVIEWER: {
        PERM_USER_VIEW,
        PERM_AUDIT_VIEW,
        PERM_SECURITY_EVENTS_READ,
    },
    CustomUser.Role.HR: {
        PERM_USER_VIEW,
        PERM_USER_CREATE,
        PERM_USER_EDIT,
        PERM_TEAM_VIEW,
        PERM_ATTENDANCE_VIEW_SELF,
        PERM_ATTENDANCE_VIEW_ALL,
        PERM_POST_CREATE,
        PERM_POST_VIEW_ALL,
        PERM_RECOVERY_DELEGATED,
    },
    CustomUser.Role.MANAGER: {
        PERM_USER_VIEW,
        PERM_TEAM_VIEW,
        PERM_ATTENDANCE_VIEW_SELF,
        PERM_ATTENDANCE_VIEW_TEAM,
        PERM_ATTENDANCE_APPROVE,
        PERM_POST_CREATE,
        PERM_POST_VIEW_TEAM,
        PERM_RECOVERY_DELEGATED,
    },
    CustomUser.Role.EMPLOYEE: {
        PERM_USER_VIEW,
        PERM_TEAM_VIEW,
        PERM_ATTENDANCE_VIEW_SELF,
        PERM_POST_CREATE,
        PERM_POST_VIEW_TEAM,
    },
    CustomUser.Role.INTERN: {
        PERM_USER_VIEW,
        PERM_ATTENDANCE_VIEW_SELF,
        PERM_POST_CREATE,
    },
}


def has_permission(user: Optional[CustomUser], perm_code: str) -> bool:
    """Evaluate whether user possesses a specific permission code. Default deny."""
    if not user or not user.is_authenticated or not user.is_active:
        return False
    allowed_perms = ROLE_PERMISSIONS.get(user.role, set())
    return perm_code in allowed_perms


# ------------------------------------------------------------------------------
# 3. OBJECT-LEVEL POLICY FUNCTIONS
# ------------------------------------------------------------------------------


def can_view_user(actor: CustomUser, target_user: CustomUser) -> bool:
    """Object policy: anyone active can view directory entry; admins/managers see extra."""
    if not actor.is_authenticated or not actor.is_active:
        return False
    return True


def can_edit_user(actor: CustomUser, target_user: CustomUser) -> bool:
    """Object policy: only Super Admin or Admin can edit other users."""
    if not actor.is_authenticated or not actor.is_active:
        return False
    if actor.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        return True
    return False


def can_approve_attendance(actor: CustomUser, session_owner: CustomUser) -> bool:
    """
    Object policy:
    1. Nobody approves their own fallback request.
    2. Admins can approve for anyone (except self).
    3. Managers can approve only for their direct reports.
    """
    if not actor.is_authenticated or not actor.is_active:
        return False
    if actor.id == session_owner.id:
        return False  # Self-approval prohibited
    if actor.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        return True
    if actor.role == CustomUser.Role.MANAGER:
        return (
            hasattr(session_owner, "manager_relationship")
            and session_owner.manager_relationship.manager_id == actor.id
        )
    return False


def can_view_team(actor: CustomUser, team_id: int) -> bool:
    """Check if actor is member, lead, or administrator of team."""
    if not actor.is_authenticated or not actor.is_active:
        return False
    if actor.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        return True
    return actor.team_memberships.filter(team_id=team_id).exists()


# ------------------------------------------------------------------------------
# 4. SUDO MODE & DECORATORS
# ------------------------------------------------------------------------------

SUDO_TIMEOUT_SECONDS = 600  # 10 minutes (aligned with SUDO_WINDOW_MINUTES)


def require_permission(perm_code: str):
    """View decorator to enforce strict permission check."""

    def decorator(view_func: Callable):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not has_permission(request.user, perm_code):
                raise PermissionDenied(f"Access denied: missing permission '{perm_code}'.")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


__all__ = [
    "PERM_BACKUP_MANAGE",
    "PERM_ATTENDANCE_APPROVE",
    "PERM_ATTENDANCE_OVERRIDE",
    "PERM_ATTENDANCE_VIEW_ALL",
    "PERM_ATTENDANCE_VIEW_SELF",
    "PERM_ATTENDANCE_VIEW_TEAM",
    "PERM_AUDIT_VIEW",
    "PERM_CERTIFICATE_ISSUE",
    "PERM_CERTIFICATE_REVOKE",
    "PERM_POST_CREATE",
    "PERM_POST_MODERATE",
    "PERM_POST_VIEW_ALL",
    "PERM_POST_VIEW_TEAM",
    "PERM_RECOVERY_ADMIN",
    "PERM_RECOVERY_DELEGATED",
    "PERM_SETTINGS_MANAGE",
    "PERM_TEAM_MANAGE",
    "PERM_TEAM_VIEW",
    "PERM_USER_CREATE",
    "PERM_USER_DEACTIVATE",
    "PERM_USER_EDIT",
    "PERM_USER_VIEW",
    "ROLE_PERMISSIONS",
    "SUDO_SESSION_KEY",
    "SUDO_TIMEOUT_SECONDS",
    "can_approve_attendance",
    "can_edit_user",
    "can_view_team",
    "can_view_user",
    "grant_sudo",
    "has_permission",
    "is_sudo_active",
    "require_permission",
    "require_sudo",
]
