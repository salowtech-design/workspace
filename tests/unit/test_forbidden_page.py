import pytest
from django.test import Client

from apps.accounts.models import CustomUser


@pytest.mark.django_db
def test_a_403_renders_the_styled_page_and_keeps_the_status_and_hides_the_reason():
    u = CustomUser.objects.create(
        employee_id="FB-1", full_name="Plain Person", email="fb-1@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(u)
    r = c.get("/attendance/admin/")
    body = r.content.decode()
    assert r.status_code == 403
    assert "You don't have access to this page" in body
    assert "Back to home" in body
    assert "<html" in body and "/static/css/tokens.css" in body  # the app layout, not a bare page
    assert "Forbidden" not in body


def _render(user, iam):
    from django.contrib.auth.models import AnonymousUser
    from django.core.exceptions import PermissionDenied
    from django.test import RequestFactory
    from django.views.defaults import permission_denied

    host = "iam.salowtech.com" if iam else "app.salowtech.com"
    req = RequestFactory().get("/anything/", HTTP_HOST=host)
    req.user = user or AnonymousUser()
    req.is_iam_host = iam
    return permission_denied(req, PermissionDenied("secret reason text"))


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["anonymous", "suspended"])
@pytest.mark.parametrize("iam", [False, True])
def test_the_403_handler_renders_for_anonymous_suspended_and_iam_host_requests(who, iam):
    u = None
    if who == "suspended":
        u = CustomUser.objects.create(
            employee_id="FB-S", full_name="Sus Pended", email="fb-s@salowtech.com",
            role="employee", status="suspended", is_active=True,
        )  # fmt: skip
    r = _render(u, iam)
    body = r.content.decode()
    assert r.status_code == 403
    assert "You don't have access to this page" in body
    assert "secret reason text" not in body
