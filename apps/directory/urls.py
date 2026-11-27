from django.urls import path

from apps.directory.views import (
    directory_list_view,
    assign_manager_view,
    employee_profile_view,
    org_chart_view,
    team_detail_view,
)

app_name = "directory"

urlpatterns = [
    path("", directory_list_view, name="list"),
    path("org/", org_chart_view, name="org"),
    path("profile/<str:employee_id>/", employee_profile_view, name="profile"),
    path("profile/<str:employee_id>/manager/", assign_manager_view, name="assign_manager"),
    path("teams/<str:team_code>/", team_detail_view, name="team_detail"),
]
