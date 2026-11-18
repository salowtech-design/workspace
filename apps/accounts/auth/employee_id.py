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
    # Employee / Engineer markers
    "EMP": {CustomUser.Role.EMPLOYEE},
    "EMPLOYEE": {CustomUser.Role.EMPLOYEE},
    "ENG": {CustomUser.Role.EMPLOYEE},
    "DEV": {CustomUser.Role.EMPLOYEE},
    "STAFF": {CustomUser.Role.EMPLOYEE},
    "BENCH": {CustomUser.Role.EMPLOYEE},
    # Admin markers
    "ADM": {CustomUser.Role.ADMIN, CustomUser.Role.SUPER_ADMIN},
    "ADMIN": {CustomUser.Role.ADMIN, CustomUser.Role.SUPER_ADMIN},
    "SADMIN": {CustomUser.Role.SUPER_ADMIN},
    "SUPERADMIN": {CustomUser.Role.SUPER_ADMIN},
    # Human Resources markers
    "HR": {CustomUser.Role.HR},
    # Manager markers
    "MGR": {CustomUser.Role.MANAGER},
    "MANAGER": {CustomUser.Role.MANAGER},
    "LEAD": {CustomUser.Role.MANAGER},
    # Intern markers
    "INT": {CustomUser.Role.INTERN},
    "INTERN": {CustomUser.Role.INTERN},
    # Security Admin markers
    "SECADMIN": {CustomUser.Role.SECURITY_ADMIN},
    "SEC_ADMIN": {CustomUser.Role.SECURITY_ADMIN},
    "SEC": {CustomUser.Role.SECURITY_ADMIN, CustomUser.Role.SECURITY_REVIEWER},
    # Security Reviewer markers
    "SECREV": {CustomUser.Role.SECURITY_REVIEWER},
    "SEC_REV": {CustomUser.Role.SECURITY_REVIEWER},
    "REVIEWER": {CustomUser.Role.SECURITY_REVIEWER},
    "REV": {CustomUser.Role.SECURITY_REVIEWER},
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
    - name-role (e.g. ST-HR -> 'HR', ALICE-DEV -> 'DEV')
    """
    normalized = normalize_employee_id(employee_id)
    if not normalized:
        return None

    parts = normalized.split("-")
    if len(parts) >= 3:
        # Format: name-role-number (e.g., ST-EMP-0001, TIDB-CONC-TEST-01)
        # Check parts from left to right for known role markers
        for part in parts[1:]:
            if part in ROLE_MARKER_MAP:
                return part
        # Default middle segment
        return parts[1]
    elif len(parts) == 2:
        # Format: name-role (e.g. ALICE-DEV) or prefix-role (e.g. ST-HR)
        if parts[1] in ROLE_MARKER_MAP:
            return parts[1]
        if parts[0] in ROLE_MARKER_MAP:
            return parts[0]
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
