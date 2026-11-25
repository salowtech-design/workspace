import pytest
from django.test import Client

from apps.accounts.models import CustomUser

HOST = "app.salowtech.com"
HX = {"HTTP_HX_REQUEST": "true"}


def _signed_in():
    u = CustomUser.objects.create(
        employee_id="HX-1", full_name="Hx One", email="hx1@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip
    c = Client(HTTP_HOST=HOST)
    c.force_login(u)
    return c


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    ["/auth/devices/approval/pending-check/", "/chat/", "/tasks/", "/calendar/", "/search/"],
)
def test_background_call_without_a_session_gets_401_not_the_sign_in_page(path):
    anon = Client(HTTP_HOST=HOST)
    r = anon.get(path, **HX)
    assert r.status_code == 401 and r.content == b""
    plain = anon.get(path)  # a normal page load still redirects to sign in
    assert plain.status_code == 302 and "/auth/login/" in plain["Location"]


@pytest.mark.django_db
def test_signed_in_background_call_is_unchanged_and_login_page_keeps_working():
    c = _signed_in()
    r = c.get("/auth/devices/approval/pending-check/", **HX)
    assert r.status_code == 200 and b"<html" not in r.content
    assert Client(HTTP_HOST=HOST).get("/auth/login/", **HX).status_code == 200
