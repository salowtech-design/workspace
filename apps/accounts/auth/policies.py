"""
Authentication & Identity Authorization Policies.
Enforces explicit role boundaries, including strict isolation of the HR role.
"""

from apps.accounts.models import CustomUser


def can_issue_activation_code(actor: CustomUser, target_user: CustomUser) -> bool:
    """
    Determine if actor can issue an activation code for target user.
    - Super Admin / Admin: Can issue for anyone.
    - HR: Can issue for Employee and Intern ONLY. Cannot issue for Admin, Super Admin, or HR.
    """
    if not actor.is_authenticated or not actor.is_active:
        return False

    if actor.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        return True

    if actor.role == CustomUser.Role.HR:
        return target_user.role in (CustomUser.Role.EMPLOYEE, CustomUser.Role.INTERN)

    return False


def can_approve_mfa_reset(actor: CustomUser, target_user: CustomUser) -> bool:
    """Only Super Admin and Admin can approve MFA resets."""
    if not actor.is_authenticated or not actor.is_active:
        return False
    return actor.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN)


def can_unlock_account(actor: CustomUser, target_user: CustomUser) -> bool:
    """Only Super Admin, Admin, and HR (for standard employees) can unlock locked accounts."""
    if not actor.is_authenticated or not actor.is_active:
        return False

    if actor.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        return True

    if actor.role == CustomUser.Role.HR:
        return target_user.role in (CustomUser.Role.EMPLOYEE, CustomUser.Role.INTERN)

    return False
