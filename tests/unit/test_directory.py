import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.accounts.models import CustomUser
from apps.directory.models import (
    Department,
    ReportingRelationship,
    Team,
    TeamMembership,
)
from apps.directory.selectors import search_directory


@pytest.mark.django_db
def test_directory_department_and_team_constraints():
    """Verify department and team creation with single-lead constraint."""
    dept = Department.objects.create(code="eng", name="Engineering")
    assert dept.code == "ENG"  # Normalized to uppercase

    lead1 = CustomUser.objects.create_user(employee_id="ENG_LEAD1", full_name="Lead One")
    CustomUser.objects.create_user(employee_id="ENG_LEAD2", full_name="Lead Two")

    team = Team.objects.create(code="backend", name="Backend Systems", department=dept, lead=lead1)
    assert team.code == "BACKEND"

    # Enforces single team per lead via OneToOneField
    with pytest.raises(IntegrityError):
        Team.objects.create(code="core", name="Core Architecture", department=dept, lead=lead1)


@pytest.mark.django_db
def test_team_duplicate_membership_prevention():
    """Verify an employee cannot be added to the same team twice."""
    dept = Department.objects.create(code="ops", name="Operations")
    team = Team.objects.create(code="infra", name="Infrastructure", department=dept)
    worker = CustomUser.objects.create_user(employee_id="OPS01", full_name="DevOps Worker")

    TeamMembership.objects.create(team=team, user=worker)

    with pytest.raises(IntegrityError):
        TeamMembership.objects.create(team=team, user=worker)


@pytest.mark.django_db
def test_reporting_hierarchy_prohibits_self_management():
    """Verify an employee cannot report to themselves."""
    user = CustomUser.objects.create_user(employee_id="SELF01", full_name="Solo Dev")

    with pytest.raises(ValidationError, match="Self-management is prohibited"):
        rel = ReportingRelationship(manager=user, subordinate=user)
        rel.clean()


@pytest.mark.django_db
def test_reporting_hierarchy_prohibits_reporting_cycles():
    """Verify cycle detection: A reports to B, B reports to C, C cannot report to A."""
    user_a = CustomUser.objects.create_user(employee_id="CYC_A", full_name="User A")
    user_b = CustomUser.objects.create_user(employee_id="CYC_B", full_name="User B")
    user_c = CustomUser.objects.create_user(employee_id="CYC_C", full_name="User C")

    # A -> B (A reports to B)
    ReportingRelationship.objects.create(manager=user_b, subordinate=user_a)
    # B -> C (B reports to C)
    ReportingRelationship.objects.create(manager=user_c, subordinate=user_b)

    # Attempt C -> A (C reports to A) creates cycle C -> A -> B -> C
    with pytest.raises(ValidationError, match="Reporting cycle detected"):
        rel = ReportingRelationship(manager=user_a, subordinate=user_c)
        rel.clean()


@pytest.mark.django_db
def test_directory_search_privacy_filtering():
    """Verify inactive/suspended employees are excluded from directory search."""
    active_user = CustomUser.objects.create_user(
        employee_id="DIR_ACT",
        full_name="Active Alice",
        status=CustomUser.Status.ACTIVE,
    )
    CustomUser.objects.create_user(
        employee_id="DIR_EXT",
        full_name="Exited Bob",
        status=CustomUser.Status.EXITED,
    )

    results = search_directory(actor=active_user)
    result_ids = [u.employee_id for u in results]

    assert "DIR_ACT" in result_ids
    assert "DIR_EXT" not in result_ids
