from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.forms import LoginForm
from apps.accounts.services import login_user, logout_user


def login_view(request):
    """
    Renders login interface and processes authentication requests.
    """
    if request.user.is_authenticated:
        return redirect("attendance:panel")

    redirect_to = request.POST.get("next") or request.GET.get("next") or ""

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data["user"]
            login_user(request, user)

            if url_has_allowed_host_and_scheme(
                url=redirect_to,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(redirect_to)
            return redirect("attendance:panel")
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form, "next": redirect_to})


def logout_view(request):
    """
    Enforces POST-only session invalidation.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"], "Logout must be requested via POST.")

    logout_user(request)
    return redirect("accounts:login")
