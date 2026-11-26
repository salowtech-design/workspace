from typing import Optional

from django.db.models import Q, QuerySet
from django.db.models.functions import Upper

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
        # Upper() on both sides: icontains is case-sensitive on TiDB's binary collation
        needle = q_clean.upper()
        qs = qs.annotate(
            _u_emp=Upper("employee_id"), _u_name=Upper("full_name"), _u_mail=Upper("email")
        ).filter(
            Q(_u_emp__contains=needle) | Q(_u_name__contains=needle) | Q(_u_mail__contains=needle)
        )

    if department_code:
        qs = qs.filter(
            team_memberships__team__department__code=department_code.strip().upper()
        ).distinct()

    return qs


ORG_MAX_DEPTH = 12  # a chart deeper than this is cut off; the cut is shown, never hidden


def org_tree(actor: CustomUser):
    """Reporting tree of active and onboarding employees. Shows only what the directory list
    already shows (ID, name, role). Returns (roots, children, truncated).

    A person whose manager is inactive, or who has no manager, is a root. Cycles cannot be
    built through the model, but the walk still refuses to revisit anyone."""
    live = list(
        CustomUser.objects.filter(
            status__in=[CustomUser.Status.ACTIVE, CustomUser.Status.ONBOARDING]
        ).order_by("full_name", "employee_id")
    )
    by_id = {u.pk: u for u in live}
    manager_of = dict(
        ReportingRelationship.objects.filter(
            subordinate_id__in=by_id.keys(), manager_id__in=by_id.keys()
        ).values_list("subordinate_id", "manager_id")
    )
    kids: dict[int, list] = {}
    roots = []
    for u in live:
        mgr = manager_of.get(u.pk)
        if mgr is None:
            roots.append(u)
        else:
            kids.setdefault(mgr, []).append(u)

    seen: set[int] = set()
    truncated = False

    def build(u, depth):
        nonlocal truncated
        seen.add(u.pk)
        if depth >= ORG_MAX_DEPTH:
            if kids.get(u.pk):
                truncated = True
            return {"user": u, "reports": []}
        return {
            "user": u,
            "reports": [build(k, depth + 1) for k in kids.get(u.pk, []) if k.pk not in seen],
        }

    tree = [build(r, 0) for r in roots]
    unreachable = [u for u in live if u.pk not in seen]  # only possible if data had a cycle
    tree += [build(u, 0) for u in unreachable if u.pk not in seen]
    return tree, truncated


def custom_values_for(viewer, user):
    """Custom field values the viewer may see. Today the only audience is administrators, so
    anyone else (including the person themselves, until the user decides) gets an empty list."""
    from apps.accounts.models import CustomUser

    if viewer.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        return []
    from .models import CustomFieldValue

    return [
        (v.definition.label, v.value)
        for v in CustomFieldValue.objects.filter(user=user).select_related("definition")
        if v.definition.visible_to == "admins"
    ]
