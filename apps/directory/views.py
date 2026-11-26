from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from apps.accounts.models import CustomUser
from apps.accounts.permissions import require_permission
from apps.directory.models import Team, TeamMembership
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

    teams = [
        m.team
        for m in TeamMembership.objects.filter(user=target_user)
        .select_related("team", "team__department")
        .order_by("team__name")
    ]
    return render(
        request,
        "directory/profile.html",
        {
            "teams": teams,
            "profile_user": target_user,
            "is_privileged": is_privileged,
            "direct_reports": direct_reports,
        },
    )


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
        },
    )


@login_required
@require_permission("accounts.user.view")
def org_chart_view(request):
    """Read-only reporting tree. Same fields as the directory list."""
    tree, truncated = org_tree(request.user)
    return render(request, "directory/org.html", {"tree": tree, "truncated": truncated})
