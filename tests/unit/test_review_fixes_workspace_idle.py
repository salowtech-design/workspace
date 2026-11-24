"""Workspace idle timeout (15 minutes) with a warning; separate from the 12 hour absolute lifetime."""
import time

import pytest
from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.auth import workspace_idle
from apps.accounts.models import AuthenticationPolicy, CustomUser


@pytest.fixture
def employee(db):
    return CustomUser.objects.create(
        email="idle.emp@salowtech.com", employee_id="EMP-WIDLE-01", full_name="Idle Employee",
        password=make_password("SalowStrongPassword2026!"), role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE, is_active=True,
    )


@pytest.fixture
def client(employee):
    c = Client()
    c.force_login(employee)
    return c


def _age(client, seconds):
    session = client.session
    session[workspace_idle.SESSION_KEY] = time.time() - seconds
    session.save()


def _last(client):
    return float(client.session[workspace_idle.SESSION_KEY])


def test_default_limit_is_fifteen_minutes_and_warning_two_minutes(db):
    assert workspace_idle.idle_limit_seconds() == 15 * 60
    assert workspace_idle.warning_seconds(15 * 60) == 120


def test_active_workspace_policy_value_is_used(db):
    AuthenticationPolicy.objects.filter(domain="workspace").update(is_active=False)
    AuthenticationPolicy.objects.create(domain="workspace", version=50, idle_session_timeout_minutes=20, is_active=True,
                                        justification="t")
    assert workspace_idle.idle_limit_seconds() == 20 * 60


def test_status_reports_remaining_and_warning(client):
    _age(client, 14 * 60)
    data = client.get("/auth/session/status/").json()
    assert 50 <= data["remaining_seconds"] <= 60
    assert data["warning_seconds"] == 120 and data["limit_seconds"] == 900


def test_status_polling_does_not_reset_idle_clock(client):
    _age(client, 10 * 60)
    before = _last(client)
    for _ in range(3):
        client.get("/auth/session/status/")
    assert _last(client) == before


def test_keepalive_resets_idle_clock(client):
    _age(client, 14 * 60)
    response = client.post("/auth/session/keepalive/")
    assert response.status_code == 200 and response.json()["remaining_seconds"] > 850


def test_keepalive_requires_post(client):
    assert client.get("/auth/session/keepalive/").status_code == 405


def test_polling_alone_ends_session_after_fifteen_minutes(client):
    _age(client, 16 * 60)
    response = client.get("/auth/session/status/")
    assert response.status_code == 302 and "reason=idle" in response["Location"]
    assert "_auth_user_id" not in client.session


def test_real_request_after_idle_limit_signs_out_and_remembers_page(client):
    _age(client, 16 * 60)
    response = client.get("/directory/")
    assert response.status_code == 302
    assert "reason=idle" in response["Location"] and "next=/directory/" in response["Location"]
    assert "_auth_user_id" not in client.session


def test_htmx_request_after_idle_limit_gets_401_with_redirect_header(client):
    _age(client, 16 * 60)
    response = client.get("/directory/", HTTP_HX_REQUEST="true")
    assert response.status_code == 401 and "reason=idle" in response["HX-Redirect"]


def test_real_request_inside_limit_resets_clock(client):
    _age(client, 10 * 60)
    before = _last(client)
    client.get("/directory/")
    assert _last(client) > before + 500
    assert "_auth_user_id" in client.session


def test_pending_device_check_polling_is_passive(client):
    _age(client, 10 * 60)
    before = _last(client)
    client.get("/auth/devices/approval/pending-check/")
    assert _last(client) == before


def test_session_without_marker_starts_its_clock_instead_of_logging_out(client):
    session = client.session
    session.pop(workspace_idle.SESSION_KEY, None)
    session.save()
    assert client.get("/directory/").status_code in (200, 302)
    assert "_auth_user_id" in client.session


def test_idle_signout_leaves_other_sessions_of_the_same_user_alone(employee):
    a, b = Client(), Client()
    a.force_login(employee)
    b.force_login(employee)
    _age(a, 16 * 60)
    a.get("/directory/")
    assert "_auth_user_id" not in a.client.session if hasattr(a, "client") else "_auth_user_id" not in a.session
    assert "_auth_user_id" in b.session


def test_banner_and_script_are_on_authenticated_workspace_pages(client):
    html = client.get("/directory/").content.decode()
    assert 'id="idle-timeout-root"' in html and "/static/session-idle.js" in html
    assert 'id="idle-timeout-banner"' in html and "Stay signed in" in html


def test_login_page_explains_idle_signout(db):
    html = Client().get("/auth/login/?reason=idle").content.decode()
    assert "signed out because of inactivity" in html
