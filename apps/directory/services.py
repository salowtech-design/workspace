from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.models import CustomUser
from apps.core.services import record_audit_event
from apps.directory.models import (
    Department,
    EmployeeProfile,
    ReportingRelationship,
    Team,
    TeamMembership,
)


def create_department(code: str, name: str, actor: CustomUser) -> Department:
    """Create a new department. Admin only."""
    if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can create departments.")

    dept = Department.objects.create(code=code.strip().upper(), name=name.strip())
    record_audit_event(
        event_type="directory.department_created",
        actor=actor,
        target_entity="Department",
        target_id=dept.code,
        details={"name": dept.name},
    )
    return dept


def create_team(
    code: str, name: str, department: Department, lead: CustomUser = None, actor: CustomUser = None
) -> Team:
    """Create a new team within a department."""
    if actor and actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can create teams.")

    team = Team.objects.create(
        code=code.strip().upper(),
        name=name.strip(),
        department=department,
        lead=lead,
    )
    record_audit_event(
        event_type="directory.team_created",
        actor=actor,
        target_entity="Team",
        target_id=team.code,
        details={"name": team.name, "department": department.code},
    )
    return team


def add_team_member(team: Team, user: CustomUser, actor: CustomUser) -> TeamMembership:
    """Add an employee to a team."""
    if actor.role not in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.MANAGER,
    ):
        raise PermissionDenied("Unauthorized to assign team memberships.")

    if TeamMembership.objects.filter(team=team, user=user).exists():
        raise ValidationError(f"{user.employee_id} is already a member of {team.name}.")

    membership = TeamMembership.objects.create(team=team, user=user)
    record_audit_event(
        event_type="directory.membership_added",
        actor=actor,
        target_entity="TeamMembership",
        target_id=str(membership.id),
        details={"team": team.code, "user": user.employee_id},
    )
    return membership


def _reporting_lock():
    """One row that every reporting change locks first, so two changes cannot each pass the
    cycle check against data the other has not committed yet. Reporting changes are rare admin
    actions, so one global lock is acceptable. (Uses a shared bucket row as the lock row.)"""
    from datetime import timedelta

    from django.utils import timezone

    from apps.core.models import RateLimitBucket

    RateLimitBucket.objects.get_or_create(
        bucket_key="lock:reporting",
        defaults={"count": 0, "reset_at": timezone.now() + timedelta(days=36500)},
    )
    return RateLimitBucket.objects.select_for_update().get(bucket_key="lock:reporting")


def _pause():
    """Test hook, called after the checks and before the write. A no-op."""


_LIVE = (CustomUser.Status.ACTIVE, CustomUser.Status.ONBOARDING)


def assign_reporting_manager(
    manager: CustomUser, subordinate: CustomUser, actor: CustomUser
) -> ReportingRelationship:
    """Assign or update direct manager for an employee.

    Authority is the existing rule in this module: super admin and admin only. (The blueprint
    proposes HR; that difference is open and is not applied here.) Refuses self-management,
    people who are not active or onboarding, and any change that would make a reporting loop.
    The loop check runs under the reporting lock on fresh data."""
    if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can configure reporting relationships.")

    with transaction.atomic():
        _reporting_lock()
        mgr = CustomUser.objects.filter(pk=manager.pk, status__in=_LIVE).first()
        sub = CustomUser.objects.filter(pk=subordinate.pk, status__in=_LIVE).first()
        if mgr is None or sub is None:
            raise ValidationError("Both people must be active or onboarding employees.")
        if mgr.pk == sub.pk:
            raise ValidationError(
                "Self-management is prohibited: an employee cannot manage themselves."
            )
        current = ReportingRelationship.objects.filter(subordinate=sub).first()
        if current is not None and current.manager_id == mgr.pk:
            return current  # nothing changes: no write, no audit row
        up, seen = mgr.pk, set()
        while up is not None and up not in seen:  # walk the manager's chain on fresh rows
            if up == sub.pk:
                raise ValidationError(
                    "Reporting cycle detected: an employee cannot report to their subordinate."
                )
            seen.add(up)
            up = (
                ReportingRelationship.objects.filter(subordinate_id=up)
                .values_list("manager_id", flat=True)
                .first()
            )
        _pause()
        rel, _ = ReportingRelationship.objects.update_or_create(
            subordinate=sub,
            defaults={"manager": mgr},
        )
        record_audit_event(
            event_type="directory.manager_assigned",
            actor=actor,
            target_entity="ReportingRelationship",
            target_id=str(rel.id),
            details={"subordinate": sub.employee_id, "manager": mgr.employee_id},
        )
        return rel


def update_employee_profile(
    target_user: CustomUser, data: dict, actor: CustomUser
) -> EmployeeProfile:
    """Update profile data. Employee can update self; Admin can update any."""
    is_self = actor.id == target_user.id
    is_admin = actor.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN)

    if not (is_self or is_admin):
        raise PermissionDenied("Unauthorized to modify this profile.")

    profile, _ = EmployeeProfile.objects.get_or_create(user=target_user)

    if "phone" in data:
        profile.phone = data["phone"].strip()
    if "emergency_contact" in data:
        profile.emergency_contact = data["emergency_contact"].strip()
    if "bio" in data:
        profile.bio = data["bio"].strip()
    if "location" in data:
        profile.location = data["location"].strip()
    if is_admin and "designation" in data:
        profile.designation = data["designation"].strip()

    profile.save()
    record_audit_event(
        event_type="directory.profile_updated",
        actor=actor,
        target_entity="EmployeeProfile",
        target_id=target_user.employee_id,
        details={"updated_fields": list(data.keys())},
    )
    return profile
