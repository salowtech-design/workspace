"""
Root URL Configuration for Salow Workspace.
"""

from django.urls import include, path

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

urlpatterns = [
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
    # Direct Security Operations Endpoints (Section 13)
    path("security/events", security_events_view, name="security_events_root"),
    path("security/events/", security_events_view, name="security_events_root_slash"),
    path("security/alerts/", security_alerts_view, name="security_alerts_root"),
    # Central IAM Platform (iam.salowtech.com)
    path("iam/", include("apps.accounts.iam_urls", namespace="iam")),
    # Application modules
    path("auth/", include("apps.accounts.urls", namespace="accounts")),
    path("directory/", include("apps.directory.urls", namespace="directory")),
    path("drive/", include("apps.drive.urls", namespace="drive")),
    path("tasks/", include("apps.tasks.urls", namespace="tasks")),
    path("calendar/", include("apps.events.urls", namespace="events")),
    path("forms/", include("apps.forms.urls", namespace="forms")),
    path("addons/", include("apps.addons.urls", namespace="addons")),
    path("search/", include("apps.finder.urls", namespace="finder")),
    path("chat/", include("apps.chat.urls", namespace="chat")),
    path("sheets/", include("apps.sheets.urls", namespace="sheets")),
    path("slides/", include("apps.slides.urls", namespace="slides")),
    path("pdf/", include("apps.pdfview.urls", namespace="pdfview")),
    path("automation/", include("apps.automation.urls", namespace="automation")),
    path("notifications/", include("apps.inbox.urls", namespace="inbox")),
    path("attendance/", include("apps.attendance.urls", namespace="attendance")),
    path("posts/", include("apps.posts.urls", namespace="posts")),
    path("certificates/", include("apps.certificates.urls", namespace="certificates")),
    # UI presentation roots
    path("", home_view, name="home"),
    path("styleguide/", styleguide_view, name="styleguide"),
]
