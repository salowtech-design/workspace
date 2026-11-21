"""
Dedicated IAM Host URL Configuration (iam.salowtech.com).
Strictly segregated from Workspace application modules and employee authentication.
"""

from django.shortcuts import redirect
from django.urls import include, path

from apps.accounts.auth.iam_auth import get_iam_session_from_request
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
    Redirects authenticated administrators to the IAM dashboard.
    """
    session = get_iam_session_from_request(request)
    if session:
        return redirect("iam:dashboard")
    return redirect("iam:login")


urlpatterns = [
    # Platform health probes
    path("health", health_probe, name="health_probe"),
    path("health/", health_probe, name="health_probe_slash"),
    path("ready", readiness_probe, name="readiness_probe"),
    path("ready/", readiness_probe, name="readiness_probe_slash"),

    # Canonical IAM Root Landing
    path("", iam_canonical_root, name="root"),

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
