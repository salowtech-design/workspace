"""
Root URL Configuration for Salow Workspace.
"""

from django.urls import include, path

from apps.core.views import health_probe, home_view, readiness_probe, styleguide_view

urlpatterns = [
    # Platform health probes
    path("health", health_probe, name="health_probe"),
    path("ready", readiness_probe, name="readiness_probe"),
    # Application modules
    path("auth/", include("apps.accounts.urls", namespace="accounts")),
    path("directory/", include("apps.directory.urls", namespace="directory")),
    path("attendance/", include("apps.attendance.urls", namespace="attendance")),
    # UI presentation roots
    path("", home_view, name="home"),
    path("styleguide/", styleguide_view, name="styleguide"),
]
