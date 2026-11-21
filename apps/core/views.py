import logging

from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render

logger = logging.getLogger(__name__)


def health_probe(request):
    """
    Process liveness probe.
    Executes without any database or network dependency.
    """
    return JsonResponse({"status": "pass"}, status=200)


def readiness_probe(request):
    """
    Dependency readiness probe.
    Executes a lightweight query against the configured TiDB database.
    Fails closed with HTTP 503 on database unavailability without leaking secrets or stack traces.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()
        return JsonResponse({"status": "pass", "database": "connected"}, status=200)
    except Exception as exc:
        logger.error("Readiness check database failure: %s", exc)
        return JsonResponse({"status": "fail", "database": "unavailable"}, status=503)


def home_view(request):
    """Root redirect handler."""
    if request.user.is_authenticated:
        return redirect("attendance:panel")
    return redirect("accounts:login")


def styleguide_view(request):
    """Visual style guide showcasing design tokens, components, and accessible states."""
    return render(request, "styleguide.html")


def service_worker_view(request):
    """
    Serves the root-scoped PWA service worker with explicit Service-Worker-Allowed header.
    Allows sw.js to control scope '/' while being safely cached.
    """
    import os
    from django.conf import settings
    from django.http import HttpResponse

    sw_path = os.path.join(settings.BASE_DIR, "static", "sw.js")
    try:
        with open(sw_path, "r", encoding="utf-8") as f:
            content = f.read()
        resp = HttpResponse(content, content_type="application/javascript; charset=utf-8")
        resp["Service-Worker-Allowed"] = "/"
        resp["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp
    except Exception as e:
        logger.error("Failed to read sw.js: %s", e)
        return HttpResponse("// sw unavailable", content_type="application/javascript", status=404)

