"""Regression tests for the step 0 repair pass (24 Nov 2026 audit)."""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import Client
from django.urls import reverse

from apps.accounts.auth.iam_auth import (
    IAM_SESSION_COOKIE_NAME,
    create_iam_admin_identity,
    create_iam_session,
)
from apps.accounts.models import CustomUser, IAMSession

User = get_user_model()
IAM_HOST = "iam.salowtech.com"
ROOT = Path(__file__).resolve().parents[2]


def _collect_names(patterns, prefix=""):
    from django.urls import URLPattern, URLResolver

    names = set()
    for entry in patterns:
        if isinstance(entry, URLResolver):
            ns = f"{prefix}{entry.namespace}:" if entry.namespace else prefix
            names |= _collect_names(entry.url_patterns, ns)
        elif isinstance(entry, URLPattern) and entry.name:
            names.add(f"{prefix}{entry.name}")
    return names


def _resolves(name: str) -> bool:
    from django.urls import get_resolver

    return any(
        name in _collect_names(get_resolver(c).url_patterns)
        for c in ("config.urls", "config.urls_iam")
    )


def test_every_template_url_name_resolves_under_a_url_config():
    """A {% url %} name that exists in no URL config crashes the page that uses it."""
    pattern = re.compile(r"""\{%\s*url\s+['"]([^'"]+)['"]""")
    missing = []
    for path in sorted((ROOT / "templates").rglob("*.html")):
        for name in pattern.findall(path.read_text(encoding="utf-8")):
            if not _resolves(name):
                missing.append(f"{path.relative_to(ROOT)}: {name}")
    assert missing == []


@pytest.fixture
def iam_client(db):
    user = User.objects.create(
        email="step0.admin@salowtech.com",
        employee_id="EMP-STEP0-01",
        full_name="Step Zero Admin",
        password=make_password("SuperSecureIamPass2026!"),
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    admin, _secret = create_iam_admin_identity(
        user=user,
        admin_id="IAM-STEP0-01",
        password="IamMasterSecretPassword2026!#",
        role="SUPER_ADMIN",
    )
    client = Client()
    fake_request = type("R", (), {"META": {"REMOTE_ADDR": "10.0.0.1"}, "headers": {}})()
    create_iam_session(admin, fake_request)
    client.cookies[IAM_SESSION_COOKIE_NAME] = fake_request.session_key_iam
    return client, admin


def test_sessions_page_renders_with_active_session_and_revoke_form_posts(iam_client):
    client, admin = iam_client
    other_request = type("R", (), {"META": {"REMOTE_ADDR": "10.0.0.9"}, "headers": {}})()
    victim = create_iam_session(admin, other_request)

    page = client.get("/iam/sessions/", HTTP_HOST=IAM_HOST)
    assert page.status_code == 200
    html = page.content.decode()
    action_url = reverse("iam:auth_control_action")
    assert f'action="{action_url}"' in html
    assert victim.session_key[:12] in html

    result = client.post(
        action_url,
        {
            "action_type": "revoke_session",
            "domain": "iam",
            "session_ref": victim.session_key,
            "reason": "step0 regression",
            "return_url": "/iam/sessions/",
        },
        HTTP_HOST=IAM_HOST,
    )
    assert result.status_code == 302
    assert result.url == "/iam/sessions/"
    victim.refresh_from_db()
    assert victim.is_active is False
    assert IAMSession.objects.filter(is_active=True).exists()


@pytest.mark.parametrize(
    "evil",
    ["https://evil.example/phish", "//evil.example/phish", "javascript:alert(1)"],
)
def test_control_action_ignores_off_site_return_url(iam_client, evil):
    client, _ = iam_client
    result = client.post(
        reverse("iam:auth_control_action"),
        {"action_type": "nonsense", "return_url": evil},
        HTTP_HOST=IAM_HOST,
    )
    assert result.status_code == 302
    assert result.url == reverse("iam:auth_landing")


def test_control_action_unexpected_error_hides_exception_text(iam_client):
    from unittest import mock

    client, _ = iam_client
    with mock.patch(
        "apps.accounts.views.authentication_centre.execute_revoke_session",
        side_effect=RuntimeError("SECRET-INTERNAL-DETAIL"),
    ):
        result = client.post(
            reverse("iam:auth_control_action"),
            {"action_type": "revoke_session", "session_ref": "x", "return_url": "/iam/sessions/"},
            HTTP_HOST=IAM_HOST,
            follow=True,
        )
    assert "SECRET-INTERNAL-DETAIL" not in result.content.decode()


def test_attendance_check_in_error_hides_exception_text(db):
    from unittest import mock

    user = User.objects.create(
        email="step0.emp@salowtech.com",
        employee_id="EMP-STEP0-02",
        full_name="Step Employee",
        password=make_password("SalowStrongPassword2026!"),
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    client = Client()
    client.force_login(user)
    with mock.patch("apps.attendance.views.check_in", side_effect=RuntimeError("SECRET-DB-HOST")):
        response = client.post("/attendance/check-in/", follow=True)
    body = response.content.decode()
    assert "SECRET-DB-HOST" not in body
    assert "Check-in failed" in body


def test_e2e_browser_test_does_not_write_into_tracked_reports_folder():
    source = (ROOT / "tests/e2e/test_playwright_e2e_browser.py").read_text(encoding="utf-8")
    assert 'Path("reports/e2e_screenshots")' not in source
    assert "E2E_SCREENSHOT_DIR" in source


@pytest.mark.parametrize("login_path", ["/iam/login/"])
def test_iam_login_cookie_is_accepted_on_the_next_request(db, login_path):
    """Real sign-in form -> cookie -> protected page. The old code stored the digest in the
    cookie, so the very next request was treated as signed out."""
    import pyotp

    user = User.objects.create(
        email="cookie.admin@salowtech.com",
        employee_id="EMP-COOKIE-01",
        full_name="Cookie Admin",
        password=make_password("SuperSecureIamPass2026!"),
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    admin, secret = create_iam_admin_identity(
        user=user,
        admin_id="IAM-COOKIE-01",
        password="IamMasterSecretPassword2026!#",
        role="SUPER_ADMIN",
    )
    client = Client()
    response = client.post(
        login_path,
        {
            "admin_id": admin.admin_id,
            "password": "IamMasterSecretPassword2026!#",
            "totp_code": pyotp.TOTP(secret).now(),
        },
        HTTP_HOST=IAM_HOST,
    )
    assert response.status_code == 302
    assert IAM_SESSION_COOKIE_NAME in client.cookies
    page = client.get("/iam/sessions/", HTTP_HOST=IAM_HOST)
    assert page.status_code == 200, page.headers.get("Location")


def test_workspace_session_rows_show_active_and_a_revoke_button(iam_client):
    """UserSession has no is_active field, so the template must not depend on one."""
    from apps.accounts.models import UserSession

    client, _ = iam_client
    employee = User.objects.create(
        email="step0.ws@salowtech.com",
        employee_id="EMP-STEP0-WS",
        full_name="Workspace Employee",
        password=make_password("SalowStrongPassword2026!"),
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    UserSession.objects.create(session_key="ws-step0-session-key", user=employee)
    page = client.get("/iam/sessions/", HTTP_HOST=IAM_HOST)
    html = page.content.decode()
    row = html[html.index("EMP-STEP0-WS") :]
    row = row[: row.index("</tr>")]
    assert "ACTIVE" in row and "TERMINATED" not in row
    assert 'value="revoke_session"' in row and 'value="workspace"' in row
