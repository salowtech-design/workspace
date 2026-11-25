"""ASGI entry for the browser drills only: serves the app and its static files from one process. Not for deployment."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test_tidb")
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402

application = ASGIStaticFilesHandler(get_asgi_application())
