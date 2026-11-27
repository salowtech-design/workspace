from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import CustomUser
from apps.accounts.permissions import PERM_TEAM_VIEW, PERM_USER_VIEW, has_permission, require_permission
from apps.directory import services
from apps.directory.models import ReportingRelationship, Team, TeamMembership
from apps.directory.selectors import (
    get_departments,
    get_direct_reports,
    org_tree,
    search_directory,
)


@login_required
@require_permission("accounts.user.view")
def directory_list_view(request):
    """Searchable company employee directory with privacy filters."""
    query = request.GET.get("q", "")
    dept_code = request.GET.get("dept", "")

    users = search_directory(request.user, query=query, department_code=dept_code)
    departments = get_departments()

    return render(
        request,
        "directory/list.html",
        {
            "users": users,
            "departments": departments,
            "query": query,
            "selected_dept": dept_code,
        },
    )


@login_required
@require_permission("accounts.user.view")
def employee_profile_view(request, employee_id: str):
    """Employee profile card with reporting line and team memberships."""
    target_user = get_object_or_404(
        CustomUser.objects.select_related("profile"),
        employee_id=employee_id.upper(),
    )
    is_privileged = request.user.role in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.MANAGER,
    )
    direct_reports = (
        get_direct_reports(target_user) if target_user.role == CustomUser.Role.MANAGER else None
    )

    # Team names, departments and links are shown only to someone who may open team pages themselves.
    teams = (
        [
            m.team
            for m in TeamMembership.objects.filter(user=target_user)
            .select_related("team", "team__department")
            .order_by("team__name")
        ]
        if has_permission(request.user, PERM_TEAM_VIEW)
        else []
    )
    is_admin = request.user.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN)
    live = (CustomUser.Status.ACTIVE, CustomUser.Status.ONBOARDING)
    rel = ReportingRelationship.objects.filter(subordinate=target_user, manager__status__in=live).select_related("manager").first()
    return render(
        request,
        "directory/profile.html",
        {
            "manager": rel.manager if rel else None,  # the same fact the org chart already shows to everyone who can open the directory
            "is_admin": is_admin,
            "manager_choices": CustomUser.objects.filter(status__in=live).exclude(pk=target_user.pk).order_by("full_name", "employee_id") if is_admin else [],
            "teams": teams,
            "profile_user": target_user,
            "is_privileged": is_privileged,
            "direct_reports": direct_reports,
        },
    )


@login_required
@require_permission("accounts.user.view")
@require_POST
def assign_manager_view(request, employee_id: str):
    """An administrator sets who a person reports to. The rule is the service's own (administrators only, no loop, no self)."""
    target = get_object_or_404(CustomUser, employee_id=employee_id.upper())
    manager = CustomUser.objects.filter(employee_id=request.POST.get("manager", "").strip().upper()).first()
    if manager is None:
        messages.error(request, "Choose a person from the list.")
    else:
        try:
            services.assign_reporting_manager(manager, target, request.user)
            messages.success(request, f"{target.full_name} now reports to {manager.full_name}.")
        except PermissionDenied:
            messages.error(request, "Only administrators can change who a person reports to.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return redirect("directory:profile", employee_id=target.employee_id)


@login_required
@require_permission("directory.team.view")
def team_detail_view(request, team_code: str):
    """Team detail view with member list."""
    team = get_object_or_404(
        Team.objects.select_related("lead", "department"),
        code=team_code.upper(),
    )
    memberships = team.memberships.select_related("user", "user__profile").all()

    return render(
        request,
        "directory/team_detail.html",
        {
            "team": team,
            "memberships": memberships,
            "can_open_profiles": has_permission(request.user, PERM_USER_VIEW),  # else plain IDs, no links
        },
    )


@login_required
@require_permission("accounts.user.view")
def org_chart_view(request):
    """Read-only reporting tree. Same fields as the directory list."""
    tree, truncated = org_tree(request.user)
    return render(request, "directory/org.html", {"tree": tree, "truncated": truncated})
