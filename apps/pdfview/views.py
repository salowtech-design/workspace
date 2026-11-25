from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache


@login_required
@never_cache
def home(request):
    """Serves the viewer page only. A PDF is opened from this device in the browser and is never
    sent to or read by the server."""
    if getattr(request.user, "status", "active") != "active":
        raise Http404
    return render(request, "pdf/home.html")
