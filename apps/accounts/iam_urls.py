from django.urls import path
from apps.accounts.views.authentication_centre import (
    auth_centre_landing_view,
    auth_control_action_view,
    auth_control_workspace_view,
    auth_iam_view,
    auth_live_activity_feed_view,
    auth_presence_feed_view,
    auth_transaction_detail_view,
    iam_heartbeat_view,
    iam_recovery_case_action_view,
    iam_recovery_portal_view,
    sessions_investigation_view,
)
from apps.accounts.views.iam_admin import (
    iam_dashboard_view,
    iam_login_view,
    iam_logout_view,
    iam_stepup_view,
)

app_name = "iam"

urlpatterns = [
    # Core IAM Auth & Session
    path("login/", iam_login_view, name="login"),
    path("logout/", iam_logout_view, name="logout"),
    path("stepup/", iam_stepup_view, name="stepup"),
    path("dashboard/", iam_dashboard_view, name="dashboard"),
    path("", iam_dashboard_view, name="root"),

    # Sequence 1 Authentication Centre Canonical Routes
    path("authentication/", auth_centre_landing_view, name="auth_landing"),
    path("authentication/control/", auth_control_workspace_view, name="auth_control"),
    path("authentication/iam/", auth_iam_view, name="auth_iam"),
    path("authentication/live-feed/", auth_live_activity_feed_view, name="auth_live_feed"),
    path("authentication/presence-feed/", auth_presence_feed_view, name="auth_presence_feed"),
    path("authentication/actions/", auth_control_action_view, name="auth_control_action"),
    path("authentication/transactions/", auth_control_workspace_view, name="auth_transactions"),
    path("authentication/transactions/<str:transaction_id>/", auth_transaction_detail_view, name="auth_transaction_detail"),
    path("authentication/policies/workspace/", auth_control_workspace_view, name="auth_policy_workspace"),
    path("authentication/policies/iam/", auth_iam_view, name="auth_policy_iam"),

    # Presence & Heartbeat
    path("heartbeat/", iam_heartbeat_view, name="heartbeat"),

    # Session Investigation
    path("sessions/", sessions_investigation_view, name="sessions"),

    # Recovery
    path("recovery/", iam_recovery_portal_view, name="recovery_portal"),
    path("recovery/actions/", iam_recovery_case_action_view, name="recovery_action"),
]

