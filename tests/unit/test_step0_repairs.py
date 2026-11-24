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


DASHBOARD_TABS = ["identity", "sessions", "threats", "investigation", "workspace_security"]


@pytest.mark.parametrize("tab", DASHBOARD_TABS + [""])
def test_iam_dashboard_renders_on_iam_host_and_every_link_stays_on_iam_urlconf(iam_client, tab):
    """/iam/dashboard/ used to 500 on the IAM host: the template reversed workspace-only names."""
    from django.urls import Resolver404, resolve

    client, _admin = iam_client
    url = "/iam/dashboard/" + (f"?tab={tab}" if tab else "")
    page = client.get(url, HTTP_HOST=IAM_HOST)
    assert page.status_code == 200
    html = page.content.decode()
    targets = re.findall(r'(?:href|action)="(/[^"#]*)"', html)
    assert targets
    for target in targets:
        if target.startswith("/static/"):
            continue
        path = target.split("?")[0]
        try:
            resolve(path, urlconf="config.urls_iam")
        except Resolver404:
            pytest.fail(f"dashboard link {target} does not exist on the IAM host")
        assert not path.startswith(("/auth/", "/attendance", "/posts")), target
    # Workspace-only actions are shown as unavailable, never as broken or broad routes.
    assert "/auth/admin/" not in html


def test_iam_dashboard_post_target_is_guarded_by_iam_session_and_csrf(iam_client):
    from django.test import Client

    client, _admin = iam_client
    html = client.get("/iam/dashboard/", HTTP_HOST=IAM_HOST).content.decode()
    assert f'action="{reverse("iam:dashboard", urlconf="config.urls_iam")}"' in html
    anon = Client(enforce_csrf_checks=True)
    denied = anon.post("/iam/dashboard/", {"tab": "identity"}, HTTP_HOST=IAM_HOST)
    assert denied.status_code in (302, 403)
    if denied.status_code == 302:
        assert "/iam/login" in denied.url
    # Workspace admin routes are still not reachable on the IAM host.
    for path in ("/auth/admin/iam/", "/auth/admin/shield/", "/auth/admin/backups/"):
        assert client.get(path, HTTP_HOST=IAM_HOST).status_code in (302, 404)


@pytest.mark.parametrize(
    "path",
    [
        "/iam-internal/",
        "/iam-internal/login/",
        "/iam-internal/dashboard/",
        "/iam-internal/sessions/",
        "/iam-internal/recovery/",
        "/iam-internal/authentication/",
    ],
)
def test_iam_views_are_not_served_on_the_workspace_host(db, path):
    """The IAM namespace is mounted at /iam-internal/ for reversing only; the workspace host must 404."""
    client = Client()
    assert client.get(path, HTTP_HOST="app.salowtech.com").status_code == 404
    assert client.post(path, {}, HTTP_HOST="app.salowtech.com").status_code == 404


def test_password_change_keeps_current_session_and_revokes_the_others(db):
    """Found live: after a valid change the user's own browser was signed out too."""
    from apps.accounts.auth.sessions import hash_session_bearer

    user = User.objects.create(
        email="pw.change@salowtech.com",
        employee_id="EMP-PWCHG-01",
        full_name="Password Changer",
        password=make_password("OldPasswordForTests2026!#"),
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    client = Client()
    client.force_login(user)
    session = client.session
    session["_salow_mfa_verified"] = True
    session.save()
    mine = hash_session_bearer(session.session_key)
    from apps.accounts.models import UserSession

    UserSession.objects.create(
        session_key=mine, session_digest=mine, user=user, ip_address="10.0.0.1", mfa_verified=True
    )
    UserSession.objects.create(
        session_key="other-device-digest",
        session_digest="other-device-digest",
        user=user,
        ip_address="10.0.0.2",
        mfa_verified=True,
    )
    new_password = "BrandNewPassw0rd2026!#Xyz"
    response = client.post(
        "/auth/security/password/change/",
        {
            "current_password": "OldPasswordForTests2026!#",
            "new_password": new_password,
            "confirm_password": new_password,
        },
    )
    assert response.status_code == 302
    assert response.url == "/auth/security/"
    user.refresh_from_db()
    assert user.check_password(new_password)
    assert client.get("/auth/security/").status_code == 200  # still signed in
    remaining = set(UserSession.objects.filter(user=user).values_list("session_key", flat=True))
    assert "other-device-digest" not in remaining
    assert mine in remaining


def test_security_page_marks_this_device_and_offers_revoke_only_for_others(db):
    """Found live: the page offered Revoke on the user's own session (raw key vs stored digest)."""
    from apps.accounts.auth.sessions import hash_session_bearer
    from apps.accounts.models import UserSession

    user = User.objects.create(
        email="sess.page@salowtech.com",
        employee_id="EMP-SESSPG-01",
        full_name="Session Page",
        password=make_password("OldPasswordForTests2026!#"),
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    client = Client()
    client.force_login(user)
    session = client.session
    session["_salow_mfa_verified"] = True
    session.save()
    mine = hash_session_bearer(session.session_key)
    UserSession.objects.create(
        session_key=mine, session_digest=mine, user=user, ip_address="10.0.0.1", mfa_verified=True
    )
    UserSession.objects.create(
        session_key="other-device-digest",
        session_digest="other-device-digest",
        user=user,
        ip_address="10.0.0.2",
        mfa_verified=True,
    )
    html = client.get("/auth/security/").content.decode()
    assert html.count("This Device") == 1
    assert "/terminate/other-device-digest" in html or "other-device-digest" in html
    assert mine not in html


def test_terminating_one_session_is_recorded_without_the_session_key(db):
    from apps.accounts.auth.sessions import end_session_by_key
    from apps.accounts.models import AuthEvent, UserSession

    user = User.objects.create(
        email="term.one@salowtech.com",
        employee_id="EMP-TERM-01",
        full_name="Terminate One",
        password=make_password("OldPasswordForTests2026!#"),
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    UserSession.objects.create(
        session_key="secret-looking-digest-value",
        session_digest="secret-looking-digest-value",
        user=user,
        ip_address="10.0.0.2",
        mfa_verified=True,
    )
    assert end_session_by_key(user, "unknown-key") is False
    assert not AuthEvent.objects.filter(event="session_terminated").exists()
    assert end_session_by_key(user, "secret-looking-digest-value") is True
    event = AuthEvent.objects.get(event="session_terminated")
    assert event.employee_id == "EMP-TERM-01"
    assert "secret-looking-digest-value" not in event.message
    assert "secret-looking-digest-value" not in (event.reference_id or "")


def test_self_service_reset_email_states_the_real_30_second_expiry(db):
    """The self-service link lives 30 seconds; the mail used to say 15 minutes."""
    import pyotp
    from django.utils import timezone

    from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
    from apps.accounts.auth.reset import request_self_service_password_reset
    from apps.accounts.models import TOTPCredential
    from apps.core.models import EmailOutbox

    user = User.objects.create(
        email="mail.ttl@salowtech.com",
        employee_id="EMP-MAILTTL-1",
        full_name="Mail Ttl",
        password=make_password("OldPasswordForTests2026!#"),
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
        email_verified=True,
    )
    secret = pyotp.random_base32(length=32)
    ct, nonce, key_id = encrypt_secret(secret, get_auth_key_ring())
    TOTPCredential.objects.create(
        user=user, encrypted_secret=ct, nonce=nonce, key_id=key_id, confirmed_at=timezone.now()
    )
    request_self_service_password_reset(
        employee_id="EMP-MAILTTL-1",
        email="mail.ttl@salowtech.com",
        totp_code=pyotp.TOTP(secret).now(),
        base_url="http://testserver",
    )
    body = EmailOutbox.objects.filter(notification_type="email_reset_link").latest("id").body_text
    assert "expires in 30 seconds" in body
    assert "15 minutes" not in body
