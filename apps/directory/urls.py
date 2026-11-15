from django.urls import path

from apps.directory.views import (
    directory_list_view,
    employee_profile_view,
    team_detail_view,
)

app_name = "directory"

urlpatterns = [
    path("", directory_list_view, name="list"),
    path("profile/<str:employee_id>/", employee_profile_view, name="profile"),
    path("teams/<str:team_code>/", team_detail_view, name="team_detail"),
]
