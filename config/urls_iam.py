"""
Dedicated IAM Host URL Configuration (iam.salowtech.com).
Strictly segregated from Workspace application modules and employee authentication.
"""

from django.shortcuts import redirect
from django.urls import include, path

from apps.accounts.auth.iam_auth import get_iam_session_from_request
from apps.accounts.views.authentication_centre import (
    auth_centre_landing_view,
    auth_control_action_view,
    auth_control_workspace_view,
    auth_iam_view,
    auth_live_activity_feed_view,
    auth_presence_feed_view,
    auth_transaction_detail_view,
    iam_heartbeat_view,
    iam_live_events_view,
    iam_live_stream_view,
    iam_recovery_case_action_view,
    iam_recovery_portal_view,
    iam_sign_in_canonical_view,
    sessions_investigation_view,
)
from apps.accounts.views.iam_admin import (
    iam_dashboard_view,
    iam_login_view,
    iam_logout_view,
    iam_stepup_view,
)
from apps.accounts.views.security_operations import (
    security_alerts_view,
    security_events_view,
)
from apps.core.views import health_probe, readiness_probe


def iam_canonical_root(request):
    """
    Canonical IAM landing router:
    Redirects unauthenticated administrators to IAM login.
    Redirects authenticated administrators to the Authentication Centre or IAM dashboard.
    """
    session = get_iam_session_from_request(request)
    if session:
        return redirect("iam:auth_landing")
    return redirect("iam_sign_in_canonical")


urlpatterns = [
    # Platform health probes
    path("health", health_probe, name="health_probe"),
    path("health/", health_probe, name="health_probe_slash"),
    path("ready", readiness_probe, name="readiness_probe"),
    path("ready/", readiness_probe, name="readiness_probe_slash"),
    # Canonical IAM Root Landing
    path("", iam_canonical_root, name="root"),
    # Canonical Sequence 1 IAM Authentication Routes (Section 3.1)
    path("auth/sign-in/", iam_sign_in_canonical_view, name="iam_sign_in_canonical"),
    path("auth/recovery/", iam_recovery_portal_view, name="iam_recovery_portal"),
    path("auth/recovery/actions/", iam_recovery_case_action_view, name="iam_recovery_action"),
    # Canonical Sequence 1 Console Routes (Section 3.1)
    path("console/", auth_centre_landing_view, name="console_root"),
    path("console/authentication/", auth_centre_landing_view, name="console_auth_landing"),
    path(
        "console/authentication/control/", auth_control_workspace_view, name="console_auth_control"
    ),
    path("console/authentication/iam/", auth_iam_view, name="console_auth_iam"),
    path(
        "console/authentication/transactions/",
        auth_control_workspace_view,
        name="console_auth_transactions",
    ),
    path(
        "console/authentication/transactions/<str:transaction_id>/",
        auth_transaction_detail_view,
        name="console_auth_transaction_detail",
    ),
    path(
        "console/authentication/policies/workspace/",
        auth_control_workspace_view,
        name="console_auth_policy_workspace",
    ),
    path("console/authentication/policies/iam/", auth_iam_view, name="console_auth_policy_iam"),
    path(
        "console/authentication/live-feed/",
        auth_live_activity_feed_view,
        name="console_auth_live_feed",
    ),
    path(
        "console/authentication/presence-feed/",
        auth_presence_feed_view,
        name="console_auth_presence_feed",
    ),
    path(
        "console/authentication/actions/",
        auth_control_action_view,
        name="console_auth_control_action",
    ),
    path("console/heartbeat/", iam_heartbeat_view, name="console_iam_heartbeat"),
    path("console/live-events/", iam_live_events_view, name="console_live_events"),
    path("console/live-stream/", iam_live_stream_view, name="console_live_stream"),
    path("console/sessions/", sessions_investigation_view, name="console_sessions"),
    # Direct Security Operations Views
    path("security/alerts/", security_alerts_view, name="security_alerts"),
    path("security/events/", security_events_view, name="security_events"),
    # Dedicated IAM Application URLs with namespace 'iam'
    path("iam/", include("apps.accounts.iam_urls", namespace="iam")),
    # Direct short paths on dedicated host
    path("login/", iam_login_view, name="login"),
    path("logout/", iam_logout_view, name="logout"),
    path("stepup/", iam_stepup_view, name="stepup"),
    path("dashboard/", iam_dashboard_view, name="dashboard"),
]
