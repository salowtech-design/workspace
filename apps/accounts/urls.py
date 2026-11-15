from django.urls import path

from apps.accounts.views.activation import (
    activate_code_view,
    activate_mfa_setup_view,
    activate_mfa_verify_view,
    activate_password_view,
    activate_recovery_codes_view,
)
from apps.accounts.views.login import (
    login_view,
    logout_view,
    mfa_challenge_view,
)
from apps.accounts.views.security import (
    console_auth_events_view,
    security_dashboard_view,
    terminate_session_view,
)

app_name = "accounts"

urlpatterns = [
    # Everyday Authentication
    path("login/", login_view, name="login"),
    path("mfa/challenge/", mfa_challenge_view, name="mfa_challenge"),
    path("logout/", logout_view, name="logout"),
    # First-Time Activation Flow
    path("activate/", activate_code_view, name="activate"),
    path("activate/password/", activate_password_view, name="activate_password"),
    path("activate/mfa/setup/", activate_mfa_setup_view, name="activate_mfa_setup"),
    path("activate/mfa/verify/", activate_mfa_verify_view, name="activate_mfa_verify"),
    path(
        "activate/recovery-codes/",
        activate_recovery_codes_view,
        name="activate_recovery_codes",
    ),
    # Security & Session Management
    path("security/", security_dashboard_view, name="security"),
    path(
        "security/sessions/<str:session_key>/terminate/",
        terminate_session_view,
        name="terminate_session",
    ),
    # Admin Audit Console
    path("console/auth-events/", console_auth_events_view, name="console_auth_events"),
]
