from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache


@login_required
@never_cache
def home(request):
    """The page only serves the grid code. A sheet is kept in this browser (localStorage, one
    key per signed-in person), never sent to or read by the server."""
    if getattr(request.user, "status", "active") != "active":
        raise Http404
    return render(request, "sheets/home.html", {"store_key": f"salow.sheet.{request.user.pk}"})
