from django.urls import path

from apps.accounts.views import (
    admin_reset_password_view,
    login_view,
    logout_view,
    mfa_setup_view,
    mfa_verify_view,
    password_change_view,
)

app_name = "accounts"

urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("mfa/verify/", mfa_verify_view, name="mfa_verify"),
    path("mfa/setup/", mfa_setup_view, name="mfa_setup"),
    path("password/change/", password_change_view, name="password_change"),
    path(
        "users/<str:employee_id>/reset-password/",
        admin_reset_password_view,
        name="admin_reset_password",
    ),
]
