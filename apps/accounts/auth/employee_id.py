"""
Employee ID Syntax Normalization & Role-Marker Consistency Engine.
Implements Section 6 specifications:
Accepted formats:
- "name-role-number" (e.g. ST-EMP-0001, JOHN-ADMIN-01)
- "name-role" (e.g. ALICE-DEV, ST-HR)
- Standard alphanumeric legacy identifiers.

Critical Security Invariants:
1. The role text inside an employee ID is NOT authorization.
2. Effective access comes only from authoritative database role grants and permissions.
3. If the parsed role marker and administrator-assigned role do not match, reject the
   attempt and create a safe security event without revealing account existence.
"""

from typing import Optional, Tuple

from apps.accounts.models import CustomUser

# Mapping of parsed role markers to authoritative CustomUser.Role values
ROLE_MARKER_MAP = {
    # Base Employee / Engineer markers (Preserved across subsequent role promotions)
    "EMP": {
        CustomUser.Role.EMPLOYEE,
        CustomUser.Role.MANAGER,
        CustomUser.Role.HR,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.ADMIN,
        CustomUser.Role.SUPER_ADMIN,
    },
    "EMPLOYEE": {
        CustomUser.Role.EMPLOYEE,
        CustomUser.Role.MANAGER,
        CustomUser.Role.HR,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.ADMIN,
        CustomUser.Role.SUPER_ADMIN,
    },
    "ENG": {
        CustomUser.Role.EMPLOYEE,
        CustomUser.Role.MANAGER,
        CustomUser.Role.HR,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.ADMIN,
        CustomUser.Role.SUPER_ADMIN,
    },
    "DEV": {
        CustomUser.Role.EMPLOYEE,
        CustomUser.Role.MANAGER,
        CustomUser.Role.HR,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.ADMIN,
        CustomUser.Role.SUPER_ADMIN,
    },
    "STAFF": {
        CustomUser.Role.EMPLOYEE,
        CustomUser.Role.MANAGER,
        CustomUser.Role.HR,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.ADMIN,
        CustomUser.Role.SUPER_ADMIN,
    },
    "BENCH": {
        CustomUser.Role.EMPLOYEE,
        CustomUser.Role.MANAGER,
        CustomUser.Role.HR,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.ADMIN,
        CustomUser.Role.SUPER_ADMIN,
    },
    # Admin markers (Privileged; cannot be held by lower roles)
    "ADM": {CustomUser.Role.ADMIN, CustomUser.Role.SUPER_ADMIN},
    "ADMIN": {CustomUser.Role.ADMIN, CustomUser.Role.SUPER_ADMIN},
    "SADMIN": {CustomUser.Role.SUPER_ADMIN},
    "SUPERADMIN": {CustomUser.Role.SUPER_ADMIN},
    # Human Resources markers
    "HR": {CustomUser.Role.HR, CustomUser.Role.ADMIN, CustomUser.Role.SUPER_ADMIN},
    # Manager markers
    "MGR": {CustomUser.Role.MANAGER, CustomUser.Role.ADMIN, CustomUser.Role.SUPER_ADMIN},
    "MANAGER": {CustomUser.Role.MANAGER, CustomUser.Role.ADMIN, CustomUser.Role.SUPER_ADMIN},
    "LEAD": {CustomUser.Role.MANAGER, CustomUser.Role.ADMIN, CustomUser.Role.SUPER_ADMIN},
    # Intern markers (can promote to employee/manager)
    "INT": {CustomUser.Role.INTERN, CustomUser.Role.EMPLOYEE, CustomUser.Role.MANAGER},
    "INTERN": {CustomUser.Role.INTERN, CustomUser.Role.EMPLOYEE, CustomUser.Role.MANAGER},
    # Security Admin markers
    "SECADMIN": {CustomUser.Role.SECURITY_ADMIN, CustomUser.Role.SUPER_ADMIN},
    "SEC_ADMIN": {CustomUser.Role.SECURITY_ADMIN, CustomUser.Role.SUPER_ADMIN},
    "SEC": {
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.SUPER_ADMIN,
    },
    # Security Reviewer markers
    "SECREV": {
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SUPER_ADMIN,
    },
    "SEC_REV": {
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SUPER_ADMIN,
    },
    "REVIEWER": {
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SUPER_ADMIN,
    },
    "REV": {
        CustomUser.Role.SECURITY_REVIEWER,
        CustomUser.Role.SECURITY_ADMIN,
        CustomUser.Role.SUPER_ADMIN,
    },
}


def normalize_employee_id(raw_id: str) -> str:
    """Strips surrounding whitespace and normalizes to uppercase."""
    if not raw_id:
        return ""
    return raw_id.strip().upper()


def parse_role_marker_from_id(employee_id: str) -> Optional[str]:
    """
    Parses the role marker segment from an employee ID.
    Supports formats:
    - name-role-number (e.g. ST-EMP-0001 -> 'EMP')
    - role-department-number (e.g. EMP-BD-001 -> 'EMP')
    - name-role (e.g. ST-HR -> 'HR', ALICE-DEV -> 'DEV')
    """
    normalized = normalize_employee_id(employee_id)
    if not normalized:
        return None

    parts = normalized.split("-")
    if len(parts) >= 3:
        # Check all parts for known role markers (prioritizing prefix or middle)
        for part in parts:
            if part.upper() in ROLE_MARKER_MAP:
                return part.upper()
        # Default middle segment
        return parts[1]
    elif len(parts) == 2:
        # Format: name-role (e.g. ALICE-DEV) or prefix-role (e.g. ST-HR)
        if parts[1].upper() in ROLE_MARKER_MAP:
            return parts[1].upper()
        if parts[0].upper() in ROLE_MARKER_MAP:
            return parts[0].upper()
        return parts[1]
    return None


def validate_employee_id_role_consistency(
    employee_id: str,
    assigned_role: str,
) -> Tuple[bool, Optional[str]]:
    """
    Validates whether the parsed role marker inside the employee ID is compatible
    with the administrator-assigned primary role in the database.

    Returns (is_valid, error_reason).
    If a role marker is recognized and does NOT match assigned_role, returns (False, reason).
    If no recognized marker is embedded (e.g. legacy alphanumeric ID), returns (True, None).
    """
    marker = parse_role_marker_from_id(employee_id)
    if not marker:
        return True, None

    marker_upper = marker.upper()
    if marker_upper not in ROLE_MARKER_MAP:
        # Unrecognized marker or standard format without role designation
        return True, None

    compatible_roles = ROLE_MARKER_MAP[marker_upper]
    if assigned_role not in compatible_roles:
        return (
            False,
            f"Employee ID marker '{marker_upper}' does not match assigned role '{assigned_role}'.",
        )

    return True, None


def reissue_employee_id(
    user: CustomUser,
    new_employee_id: str,
    actor: CustomUser,
    reason: str,
) -> CustomUser:
    """
    Audited administrative re-issuance of employee ID upon role promotion or organizational re-alignment.
    Requires actor to be SUPER_ADMIN or ADMIN.
    """
    from django.core.exceptions import PermissionDenied, ValidationError

    from apps.accounts.auth.audit import log_auth_event

    if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators may re-issue employee identifiers.")

    if not reason or not reason.strip():
        raise ValidationError(
            "A documented justification reason is required to re-issue an employee ID."
        )

    normalized_new_id = normalize_employee_id(new_employee_id)
    if not normalized_new_id:
        raise ValidationError("New employee ID cannot be empty.")

    old_id = user.employee_id
    if old_id == normalized_new_id:
        return user

    if CustomUser.objects.filter(employee_id=normalized_new_id).exclude(pk=user.pk).exists():
        raise ValidationError(
            f"Employee ID '{normalized_new_id}' is already assigned to another account."
        )

    user._allow_employee_id_change = True
    user.employee_id = normalized_new_id
    user.save(update_fields=["employee_id", "updated_at"])

    log_auth_event(
        event="employee_id_reissued",
        code="AUTH-ADMIN-REISSUE",
        step="reissue_employee_id",
        employee_id=normalized_new_id,
        reference_id=f"REISSUE-{old_id}->{normalized_new_id}",
        message=f"Employee ID reissued from {old_id} to {normalized_new_id} by {actor.employee_id}. Reason: {reason}",
    )
    return user
