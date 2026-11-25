"""
Dedicated Workspace Host URL Configuration (app.salowtech.com).
Serves employee-facing business and security modules.
Segregated from administrative IAM control plane.
"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import include, path

from apps.accounts.views.activation import activate_code_view
from apps.accounts.views.login import login_view
from apps.accounts.views.recovery import forgot_password_view
from apps.accounts.views.security_operations import (
    security_alerts_view,
    security_events_view,
)
from apps.certificates.views import public_verify_view
from apps.core.views import (
    health_probe,
    home_view,
    readiness_probe,
    service_worker_view,
    styleguide_view,
)


def redirect_to_iam_host(request):
    """Controlled redirect from Workspace host to dedicated IAM host."""
    iam_host = getattr(settings, "IAM_HOSTNAME", "iam.salowtech.com")
    return redirect(f"https://{iam_host}{request.get_full_path()}")


urlpatterns = [
    # Sequence 1 Canonical Workspace Routes (Section 3.1)
    path("login/", activate_code_view, name="canonical_login_activate"),
    path("sign-in/", login_view, name="canonical_signin"),
    path("forgot-password/", forgot_password_view, name="canonical_forgot_password"),
    # Platform health probes
    path("health", health_probe, name="health_probe"),
    path("health/", health_probe, name="health_probe_slash"),
    path("ready", readiness_probe, name="readiness_probe"),
    path("ready/", readiness_probe, name="readiness_probe_slash"),
    # Root-scoped PWA Service Worker
    path("sw.js", service_worker_view, name="service_worker"),
    # Public verification portal
    path("verify/", public_verify_view, name="public_verify"),
    path("verify/<str:identifier>/", public_verify_view, name="public_verify_id"),
    # Direct Security Operations Endpoints (read-only for workspace self-service)
    path("security/events", security_events_view, name="security_events_root"),
    path("security/events/", security_events_view, name="security_events_root_slash"),
    path("security/alerts/", security_alerts_view, name="security_alerts_root"),
    # Application modules
    path("auth/", include("apps.accounts.urls", namespace="accounts")),
    path("directory/", include("apps.directory.urls", namespace="directory")),
    path("drive/", include("apps.drive.urls", namespace="drive")),
    path("attendance/", include("apps.attendance.urls", namespace="attendance")),
    path("posts/", include("apps.posts.urls", namespace="posts")),
    path("certificates/", include("apps.certificates.urls", namespace="certificates")),
    # Controlled redirect for IAM admin paths on Workspace host
    path("iam/", redirect_to_iam_host, name="iam_redirect"),
    path("iam/<path:subpath>", redirect_to_iam_host, name="iam_subpath_redirect"),
    # Included for URL reversing compatibility across tests
    path("iam-internal/", include("apps.accounts.iam_urls", namespace="iam")),
    # UI presentation roots
    path("", home_view, name="home"),
    path("styleguide/", styleguide_view, name="styleguide"),
]
