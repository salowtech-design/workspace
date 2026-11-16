"""
Root URL Configuration for Salow Workspace.
"""

from django.urls import include, path

from apps.certificates.views import public_verify_view
from apps.core.views import health_probe, home_view, readiness_probe, styleguide_view

urlpatterns = [
    # Platform health probes
    path("health", health_probe, name="health_probe"),
    path("health/", health_probe, name="health_probe_slash"),
    path("ready", readiness_probe, name="readiness_probe"),
    path("ready/", readiness_probe, name="readiness_probe_slash"),
    # Public verification portal
    path("verify/", public_verify_view, name="public_verify"),
    path("verify/<str:identifier>/", public_verify_view, name="public_verify_id"),
    # Application modules
    path("auth/", include("apps.accounts.urls", namespace="accounts")),
    path("directory/", include("apps.directory.urls", namespace="directory")),
    path("attendance/", include("apps.attendance.urls", namespace="attendance")),
    path("posts/", include("apps.posts.urls", namespace="posts")),
    path("certificates/", include("apps.certificates.urls", namespace="certificates")),
    # UI presentation roots
    path("", home_view, name="home"),
    path("styleguide/", styleguide_view, name="styleguide"),
]
