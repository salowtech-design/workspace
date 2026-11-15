import pytest

from apps.accounts.models import CustomUser
from apps.accounts.permissions import (
    PERM_ATTENDANCE_APPROVE,
    PERM_ATTENDANCE_VIEW_ALL,
    PERM_ATTENDANCE_VIEW_SELF,
    PERM_CERTIFICATE_ISSUE,
    PERM_POST_CREATE,
    PERM_USER_DEACTIVATE,
    can_approve_attendance,
    has_permission,
)
from apps.directory.models import ReportingRelationship


@pytest.mark.django_db
def test_role_permission_matrix():
    """Verify role permissions enforce default-deny and appropriate role boundaries."""
    super_admin = CustomUser.objects.create_user(
        employee_id="SA01", full_name="Super Admin", role=CustomUser.Role.SUPER_ADMIN
    )
    admin = CustomUser.objects.create_user(
        employee_id="ADM01", full_name="Admin User", role=CustomUser.Role.ADMIN
    )
    manager = CustomUser.objects.create_user(
        employee_id="MGR01", full_name="Team Manager", role=CustomUser.Role.MANAGER
    )
    employee = CustomUser.objects.create_user(
        employee_id="EMP01", full_name="Standard Employee", role=CustomUser.Role.EMPLOYEE
    )
    intern = CustomUser.objects.create_user(
        employee_id="INT01", full_name="Intern Developer", role=CustomUser.Role.INTERN
    )

    # Super Admin & Admin can deactivate users and issue certificates
    assert has_permission(super_admin, PERM_USER_DEACTIVATE) is True
    assert has_permission(admin, PERM_CERTIFICATE_ISSUE) is True

    # Manager can approve attendance but cannot deactivate users
    assert has_permission(manager, PERM_ATTENDANCE_APPROVE) is True
    assert has_permission(manager, PERM_USER_DEACTIVATE) is False
    assert has_permission(manager, PERM_CERTIFICATE_ISSUE) is False

    # Employee can view self attendance and create posts, but cannot approve attendance
    assert has_permission(employee, PERM_ATTENDANCE_VIEW_SELF) is True
    assert has_permission(employee, PERM_POST_CREATE) is True
    assert has_permission(employee, PERM_ATTENDANCE_APPROVE) is False
    assert has_permission(employee, PERM_ATTENDANCE_VIEW_ALL) is False

    # Intern has strictly scoped permissions
    assert has_permission(intern, PERM_ATTENDANCE_VIEW_SELF) is True
    assert has_permission(intern, PERM_ATTENDANCE_APPROVE) is False
    assert has_permission(intern, PERM_ATTENDANCE_VIEW_ALL) is False

    # Default deny for unauthenticated or deactivated user
    employee.is_active = False
    assert has_permission(employee, PERM_ATTENDANCE_VIEW_SELF) is False
    assert has_permission(None, PERM_POST_CREATE) is False


@pytest.mark.django_db
def test_attendance_approval_object_policy():
    """Verify nobody approves self, manager approves direct reports, admin approves any."""
    manager = CustomUser.objects.create_user(
        employee_id="MGR02", full_name="Alice Manager", role=CustomUser.Role.MANAGER
    )
    direct_report = CustomUser.objects.create_user(
        employee_id="EMP02", full_name="Bob Worker", role=CustomUser.Role.EMPLOYEE
    )
    other_worker = CustomUser.objects.create_user(
        employee_id="EMP03", full_name="Charlie Worker", role=CustomUser.Role.EMPLOYEE
    )
    admin = CustomUser.objects.create_user(
        employee_id="ADM02", full_name="Admin Dave", role=CustomUser.Role.ADMIN
    )

    # Setup direct reporting relationship
    ReportingRelationship.objects.create(manager=manager, subordinate=direct_report)

    # 1. Self-approval strictly prohibited
    assert can_approve_attendance(manager, manager) is False
    assert can_approve_attendance(admin, admin) is False
    assert can_approve_attendance(direct_report, direct_report) is False

    # 2. Manager can approve direct report
    assert can_approve_attendance(manager, direct_report) is True

    # 3. Manager CANNOT approve non-report
    assert can_approve_attendance(manager, other_worker) is False

    # 4. Admin can approve for any employee (except self)
    assert can_approve_attendance(admin, direct_report) is True
    assert can_approve_attendance(admin, other_worker) is True
