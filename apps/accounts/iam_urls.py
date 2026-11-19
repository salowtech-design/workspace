from django.urls import path
from apps.accounts.views.iam_admin import (
    iam_dashboard_view,
    iam_login_view,
    iam_logout_view,
    iam_stepup_view,
)

app_name = "iam"

urlpatterns = [
    path("login/", iam_login_view, name="login"),
    path("logout/", iam_logout_view, name="logout"),
    path("stepup/", iam_stepup_view, name="stepup"),
    path("dashboard/", iam_dashboard_view, name="dashboard"),
    path("", iam_dashboard_view, name="root"),
]
