import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.addons import drafts as ds
from apps.core.foundation.throttle import throttled


def _errors(request, exc):
    for m in exc.messages:
        messages.error(request, m)


@login_required
@require_http_methods(["GET", "POST"])
def home(request):
    if request.method == "POST":
        if throttled(request.user, "addons-write", 30):
            messages.error(request, "Too many changes in a minute. Wait and try again.")
        else:
            try:
                a = ds.create_addon(request.user, request.POST.get("name", ""))
                return redirect("addons:draft", pid=a.public_id)
            except ValidationError as exc:
                _errors(request, exc)
    return render(request, "addons/home.html", {"items": ds.mine(request.user)})


@login_required
@require_http_methods(["GET", "POST"])
def draft(request, pid):
    addon = ds.get_mine(request.user, pid)
    text = None
    if request.method == "POST":
        text = request.POST.get("manifest", "")
        if throttled(request.user, "addons-write", 30):
            messages.error(request, "Too many changes in a minute. Wait and try again.")
        else:
            try:
                v = ds.save_version(request.user, pid, text)
                messages.success(request, "Draft %s saved. Nothing was submitted or turned on." % v.version)
                return redirect(reverse("addons:draft", args=[pid]) + "?v=" + v.version)
            except ValidationError as exc:
                _errors(request, exc)
    versions = list(addon.versions.order_by("-created_at", "-pk"))
    if text is None:  # show the saved manifest of the chosen (else newest) version, so a later save never replaces it with the starter
        chosen = next((v for v in versions if v.version == request.GET.get("v")), versions[0] if versions else None)
        text = json.dumps(chosen.manifest, indent=2) if chosen else ds.starter(addon)
    return render(request, "addons/draft.html", {"a": addon, "versions": versions, "text": text})


@login_required
def review(request, pid, version):
    addon = ds.get_mine(request.user, pid)
    v = addon.versions.filter(version=version).first()
    if v is None:
        from django.http import Http404

        raise Http404
    return render(request, "addons/review.html", {"a": addon, "v": v, "m": v.manifest})
