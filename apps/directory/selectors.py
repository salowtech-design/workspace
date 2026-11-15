from typing import Optional

from django.db.models import Q, QuerySet

from apps.accounts.models import CustomUser
from apps.directory.models import Department, ReportingRelationship, Team


def get_departments() -> QuerySet[Department]:
    """Retrieve all departments ordered by name."""
    return Department.objects.all().order_by("name")


def get_teams_by_department(department_id: int) -> QuerySet[Team]:
    """Retrieve teams within a department."""
    return Team.objects.filter(department_id=department_id).select_related("lead").order_by("name")


def get_direct_reports(manager: CustomUser) -> QuerySet[CustomUser]:
    """Retrieve all direct reports for a given manager."""
    subordinate_ids = ReportingRelationship.objects.filter(manager=manager).values_list(
        "subordinate_id", flat=True
    )
    return (
        CustomUser.objects.filter(id__in=subordinate_ids)
        .select_related("profile")
        .order_by("employee_id")
    )


def search_directory(
    actor: CustomUser,
    query: Optional[str] = None,
    department_code: Optional[str] = None,
) -> QuerySet[CustomUser]:
    """
    Search active employees in directory.
    Enforces privacy: non-privileged users cannot query sensitive personal details.
    """
    qs = (
        CustomUser.objects.filter(
            status__in=[CustomUser.Status.ACTIVE, CustomUser.Status.ONBOARDING]
        )
        .select_related("profile")
        .order_by("employee_id")
    )

    if query:
        q_clean = query.strip()
        qs = qs.filter(
            Q(employee_id__icontains=q_clean)
            | Q(full_name__icontains=q_clean)
            | Q(email__icontains=q_clean)
        )

    if department_code:
        qs = qs.filter(
            team_memberships__team__department__code=department_code.strip().upper()
        ).distinct()

    return qs
