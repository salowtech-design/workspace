"""IAM admin sessions expire after 5 minutes of inactivity; absolute expiry is separate (8 hours)."""

from datetime import timedelta

import pytest
from django.contrib.auth.hashers import make_password
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.auth import iam_auth
from apps.accounts.auth.iam_auth import (
    IAM_SESSION_ABSOLUTE_TIMEOUT_HOURS,
    IAM_SESSION_COOKIE_NAME,
    IAM_SESSION_IDLE_TIMEOUT_MINUTES,
    create_iam_admin_identity,
    create_iam_session,
    get_iam_session_from_request,
)
from apps.accounts.models import CustomUser, IAMAdminIdentity, IAMSession


@pytest.fixture
def session_and_token(db):
    user = CustomUser.objects.create(
        email="idle.admin@salowtech.com",
        employee_id="EMP-IDLE-01",
        full_name="Idle Admin",
        password=make_password("SuperSecureIamPass2026!"),
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    admin, _ = create_iam_admin_identity(
        user=user,
        admin_id="IAM-IDLE-01",
        password="IamMasterSecretPassword2026!#",
        role=IAMAdminIdentity.Role.SUPER_ADMIN,
    )
    setup = RequestFactory().get("/")
    session = create_iam_session(admin, setup)
    return session, setup.session_key_iam


def _request(token, path="/console/"):
    request = RequestFactory().get(path)
    request.COOKIES[IAM_SESSION_COOKIE_NAME] = token
    return request


def _set_times(session, last_activity=None, created_at=None):
    fields = {}
    if last_activity is not None:
        fields["last_activity"] = last_activity
    if created_at is not None:
        fields["created_at"] = created_at
    IAMSession.objects.filter(pk=session.pk).update(**fields)  # update() bypasses auto_now


def test_idle_timeout_is_five_minutes():
    assert IAM_SESSION_IDLE_TIMEOUT_MINUTES == 5
    assert IAM_SESSION_ABSOLUTE_TIMEOUT_HOURS == 8


def test_session_valid_just_inside_five_minutes(session_and_token):
    session, token = session_and_token
    _set_times(session, last_activity=timezone.now() - timedelta(minutes=4, seconds=50))
    assert get_iam_session_from_request(_request(token)) is not None


def test_session_expires_after_five_minutes_idle(session_and_token):
    session, token = session_and_token
    _set_times(session, last_activity=timezone.now() - timedelta(minutes=5, seconds=10))
    assert get_iam_session_from_request(_request(token)) is None
    session.refresh_from_db()
    assert session.is_active is False


def test_real_request_resets_idle_clock(session_and_token):
    session, token = session_and_token
    _set_times(session, last_activity=timezone.now() - timedelta(minutes=4))
    assert get_iam_session_from_request(_request(token)) is not None
    session.refresh_from_db()
    assert timezone.now() - session.last_activity < timedelta(seconds=30)


@pytest.mark.parametrize("path", iam_auth.IAM_PASSIVE_PATH_PREFIXES)
def test_background_polling_does_not_reset_idle_clock(session_and_token, path):
    session, token = session_and_token
    stale = timezone.now() - timedelta(minutes=4)
    _set_times(session, last_activity=stale)
    assert get_iam_session_from_request(_request(token, path + "/")) is not None
    session.refresh_from_db()
    assert abs((session.last_activity - stale).total_seconds()) < 1


def test_polling_alone_cannot_keep_session_past_five_minutes(session_and_token):
    session, token = session_and_token
    _set_times(session, last_activity=timezone.now() - timedelta(minutes=6))
    assert (
        get_iam_session_from_request(_request(token, "/console/authentication/live-feed/")) is None
    )


def test_absolute_expiry_is_separate_from_idle(session_and_token):
    """An active admin is still signed out after 8 hours."""
    session, token = session_and_token
    _set_times(
        session,
        last_activity=timezone.now(),
        created_at=timezone.now() - timedelta(hours=8, minutes=1),
    )
    assert get_iam_session_from_request(_request(token)) is None


def test_default_iam_policy_idle_baseline_is_five(db):
    from apps.accounts.auth.authentication_centre import get_active_policy
    from apps.accounts.models import AuthenticationPolicy

    policy = get_active_policy(AuthenticationPolicy.Domain.IAM)
    assert policy.idle_session_timeout_minutes == 5
    assert policy.absolute_session_timeout_hours == 8


# --- Policy consistency: stored policy value, displayed value and enforced value agree ---


def _legacy_iam_policy(idle):
    from apps.accounts.models import AuthenticationPolicy

    AuthenticationPolicy.objects.filter(domain="iam").update(is_active=False)
    return AuthenticationPolicy.objects.create(
        domain="iam",
        version=99,
        idle_session_timeout_minutes=idle,
        absolute_session_timeout_hours=8,
        password_min_length=16,
        is_active=True,
        justification="test",
    )


def test_legacy_policy_of_ten_is_displayed_and_enforced_as_five(session_and_token):
    session, token = session_and_token
    policy = _legacy_iam_policy(10)
    assert policy.effective_idle_session_timeout_minutes == 5
    _set_times(session, last_activity=timezone.now() - timedelta(minutes=6))
    assert get_iam_session_from_request(_request(token)) is None


def test_admin_can_lower_iam_idle_below_five_and_it_is_enforced(session_and_token):
    session, token = session_and_token
    _legacy_iam_policy(3)
    _set_times(session, last_activity=timezone.now() - timedelta(minutes=4))
    assert get_iam_session_from_request(_request(token)) is None


def test_iam_policy_cannot_be_saved_above_five(db, session_and_token):
    from django.core.exceptions import ValidationError

    from apps.accounts.auth.authentication_centre import save_policy_version

    kwargs = dict(
        domain="iam",
        password_min_length=16,
        failed_login_threshold=3,
        lockout_window_minutes=15,
        lockout_duration_minutes=15,
        absolute_session_timeout_hours=8,
        mfa_required=True,
        device_approval_required=False,
        editor_user=None,
        justification="why",
    )
    with pytest.raises(ValidationError):
        save_policy_version(idle_session_timeout_minutes=6, **kwargs)
    assert (
        save_policy_version(idle_session_timeout_minutes=5, **kwargs).idle_session_timeout_minutes
        == 5
    )


def test_workspace_policy_idle_is_not_capped(db):
    from apps.accounts.models import AuthenticationPolicy

    p = AuthenticationPolicy(domain="workspace", idle_session_timeout_minutes=15)
    assert p.effective_idle_session_timeout_minutes == 15


# --- Full request path (middleware, urls, views) on the IAM host ---


def _iam_client(token):
    from django.conf import settings
    from django.test import Client

    client = Client(HTTP_HOST=getattr(settings, "IAM_HOSTNAME", "iam.salowtech.com"))
    client.cookies[IAM_SESSION_COOKIE_NAME] = token
    return client


def test_http_polling_alone_cannot_keep_admin_signed_in(session_and_token):
    """Simulates an unattended dashboard: only background polls arrive, and the session still ends."""
    session, token = session_and_token
    client = _iam_client(token)
    feed = "/console/authentication/live-feed/?domain=iam"
    hb = "/console/heartbeat/"

    _set_times(session, last_activity=timezone.now() - timedelta(minutes=4))
    stale = IAMSession.objects.get(pk=session.pk).last_activity
    assert client.get(feed).status_code == 200
    assert client.get("/console/authentication/presence-feed/").status_code == 200
    assert client.post(hb, {"page_key": "console"}).status_code == 200
    assert abs((IAMSession.objects.get(pk=session.pk).last_activity - stale).total_seconds()) < 1

    _set_times(session, last_activity=timezone.now() - timedelta(minutes=5, seconds=30))
    response = client.get(feed)
    assert response.status_code == 302 and "login" in response["Location"]
    assert IAMSession.objects.get(pk=session.pk).is_active is False


def test_http_real_page_request_keeps_admin_signed_in(session_and_token):
    session, token = session_and_token
    client = _iam_client(token)
    _set_times(session, last_activity=timezone.now() - timedelta(minutes=4))
    assert client.get("/console/authentication/").status_code in (200, 302)
    refreshed = IAMSession.objects.get(pk=session.pk)
    assert refreshed.is_active is True
    assert timezone.now() - refreshed.last_activity < timedelta(seconds=30)
