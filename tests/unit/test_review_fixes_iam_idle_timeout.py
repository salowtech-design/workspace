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
from apps.accounts.models import AuthEvent, CustomUser, IAMAdminIdentity, IAMSession


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

    kwargs = {
        "domain": "iam",
        "password_min_length": 16,
        "failed_login_threshold": 3,
        "lockout_window_minutes": 15,
        "lockout_duration_minutes": 15,
        "absolute_session_timeout_hours": 8,
        "mfa_required": True,
        "device_approval_required": False,
        "editor_user": None,
        "justification": "why",
    }
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


# --- Polls at the paths the templates actually reverse to, and the live action feed ---


def _fresh_idle(session):
    _set_times(session, last_activity=timezone.now() - timedelta(minutes=4))
    return IAMSession.objects.get(pk=session.pk).last_activity


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/iam/authentication/live-feed/?domain=iam"),
        ("get", "/iam/authentication/presence-feed/"),
        ("post", "/iam/heartbeat/"),
        ("get", "/iam/live-events/"),
    ],
)
def test_polls_on_iam_app_paths_do_not_extend_idle(session_and_token, method, path):
    session, token = session_and_token
    client = _iam_client(token)
    stale = _fresh_idle(session)
    getattr(client, method)(path)
    assert abs((IAMSession.objects.get(pk=session.pk).last_activity - stale).total_seconds()) < 1


def _emit(event, actor="", subject="", message="m"):
    from apps.accounts.auth.audit import log_auth_event

    return log_auth_event(
        event=event,
        code="INFO",
        step="t",
        employee_id=subject,
        actor_employee_id=actor,
        message=message,
        network_address="203.0.113.9",
    )


def test_live_events_requires_an_active_iam_session(session_and_token, client):
    from apps.accounts.auth.live_events import reset_snapshot

    reset_snapshot()
    session, token = session_and_token
    assert client.get("/iam/live-events/").status_code in (401, 302)  # no IAM cookie
    ic = _iam_client(token)
    assert ic.get("/iam/live-events/").status_code == 200
    IAMSession.objects.filter(pk=session.pk).update(is_active=False)  # revoked
    revoked = ic.get("/iam/live-events/")
    assert revoked.status_code in (401, 302) and b"events" not in revoked.content


def test_live_events_cursor_dedup_actor_domain_and_no_leaks(session_and_token):
    from apps.accounts.auth.live_events import reset_snapshot

    session, token = session_and_token
    ic = _iam_client(token)
    reset_snapshot()
    e1 = _emit("team_membership_added", actor="ST-ACT1", subject="ST-SUB1")
    e2 = _emit("password_verified", subject="ST-SUB2")
    reset_snapshot()
    first = ic.get("/iam/live-events/?limit=100").json()
    ids = [e["id"] for e in first["events"]]
    assert e1.id in ids and e2.id in ids
    row = next(e for e in first["events"] if e["id"] == e1.id)
    assert row["actor"] == "ST-ACT1" and row["subject"] == "ST-SUB1" and row["domain"] == "iam"
    assert set(row) == {
        "id",
        "time",
        "domain",
        "event",
        "code",
        "actor",
        "subject",
        "reference",
        "message",
    }
    assert "203.0.113.9" not in str(first)
    # Reconnect with the cursor: nothing is repeated.
    reset_snapshot()
    again = ic.get(f"/iam/live-events/?after={first['cursor']}").json()
    assert again["events"] == [] and again["cursor"] == first["cursor"]
    # A new event shows up exactly once, and the domain filter applies.
    e3 = _emit("sessions_revoked", actor="ST-ACT2", subject="ST-SUB3")
    reset_snapshot()
    nxt = ic.get(f"/iam/live-events/?after={first['cursor']}").json()
    assert [e["id"] for e in nxt["events"]] == [e3.id]
    only_ws = ic.get("/iam/live-events/?domain=workspace&limit=100").json()
    assert all(e["domain"] == "workspace" for e in only_ws["events"])
    # Last-Event-ID header works like ?after=
    reset_snapshot()
    hdr = ic.get("/iam/live-events/", HTTP_LAST_EVENT_ID=str(first["cursor"])).json()
    assert [e["id"] for e in hdr["events"]] == [e3.id]


def test_live_events_message_is_allow_listed_not_truncated(session_and_token):
    from apps.accounts.auth.live_events import reset_snapshot

    _, token = session_and_token
    ic = _iam_client(token)
    _emit("password_verified", subject="ST-X", message="reset link http://x/abc123 token=SECRET")
    _emit("team_membership_added", actor="ST-A", subject="ST-X", message="Added to team T1 by ST-A")
    reset_snapshot()
    rows = ic.get("/iam/live-events/?limit=100").json()["events"]
    by = {r["event"]: r for r in rows}
    assert by["password_verified"]["message"] == ""
    assert "SECRET" not in str(rows)
    assert by["team_membership_added"]["message"].startswith("Added to team")
    # A sign-in style event names its employee as the actor.
    assert by["password_verified"]["actor"] == "ST-X"


def test_unknown_event_names_are_unclassified_not_workspace(session_and_token):
    from apps.accounts.auth.live_events import domain_of, reset_snapshot

    _, token = session_and_token
    ic = _iam_client(token)
    assert domain_of("brand_new_event_name") == "unclassified"
    e = _emit("brand_new_event_name", subject="ST-Y")
    reset_snapshot()
    un = ic.get("/iam/live-events/?domain=unclassified&limit=100").json()["events"]
    assert e.id in [r["id"] for r in un]
    ws = ic.get("/iam/live-events/?domain=workspace&limit=100").json()["events"]
    assert e.id not in [r["id"] for r in ws]


def test_every_event_name_the_code_emits_has_a_domain():
    import pathlib
    import re

    from apps.accounts.auth.live_events import domain_of

    names = set()
    for f in pathlib.Path("apps").rglob("*.py"):
        if "tests" in f.parts or "migrations" in f.parts:
            continue
        names.update(re.findall(r'event="([a-zA-Z0-9_.]+)"', f.read_text()))
    from apps.accounts.auth.live_events import is_transaction_event

    # Transaction events (auth.requested, auth.terminal.*) take their domain from the
    # transaction row, so give them one here; every other name must classify by itself.
    missing = sorted(
        n
        for n in names
        if domain_of(n, "iam" if is_transaction_event(n) else None) == "unclassified"
    )
    assert not missing, f"unclassified event names: {missing}"


def test_cursor_older_than_snapshot_window_reads_the_gap(session_and_token, monkeypatch):
    from apps.accounts.auth import live_events as le

    _, token = session_and_token
    ic = _iam_client(token)
    monkeypatch.setattr(le, "SNAPSHOT_SIZE", 3)
    first = _emit("team_membership_added", actor="ST-A", subject="ST-1")
    made = [_emit("team_membership_added", actor="ST-A", subject=f"ST-{i}") for i in range(2, 8)]
    le.reset_snapshot()
    got = ic.get(f"/iam/live-events/?after={first.id}&limit=100").json()
    assert [e["id"] for e in got["events"]] == [m.id for m in made]
    assert got["cursor"] == made[-1].id


def test_suspended_admin_stops_receiving_events_mid_session(session_and_token):
    from apps.accounts.auth.live_events import reset_snapshot

    session, token = session_and_token
    ic = _iam_client(token)
    reset_snapshot()
    assert ic.get("/iam/live-events/").status_code == 200
    user = session.iam_admin.user
    user.status = CustomUser.Status.SUSPENDED
    user.save(update_fields=["status"])
    resp = ic.get("/iam/live-events/")
    assert resp.status_code in (401, 302) and b"events" not in resp.content
    assert IAMSession.objects.get(pk=session.pk).is_active is False


def test_event_written_by_another_process_appears_within_the_snapshot_ttl(
    session_and_token, monkeypatch
):
    from apps.accounts.auth import live_events as le
    from apps.accounts.models import AuthEvent

    _, token = session_and_token
    ic = _iam_client(token)
    le.reset_snapshot()
    monkeypatch.setattr(le, "SNAPSHOT_TTL", 60.0)
    cursor = ic.get("/iam/live-events/").json()["cursor"]
    # Another worker writes straight to the database; this process has not refreshed yet.
    ev = AuthEvent.objects.create(
        event="team_membership_added",
        code="INFO",
        step="x",
        reference_id="r",
        actor_employee_id="ST-Z",
    )
    assert ic.get(f"/iam/live-events/?after={cursor}").json()["events"] == []  # bounded staleness
    monkeypatch.setattr(le, "SNAPSHOT_TTL", 0.0)
    seen = ic.get(f"/iam/live-events/?after={cursor}").json()["events"]
    assert [e["id"] for e in seen] == [ev.id]


def test_live_events_response_is_not_cacheable(session_and_token):
    _, token = session_and_token
    resp = _iam_client(token).get("/iam/live-events/")
    assert resp["Cache-Control"] == "no-store"


def test_break_glass_reason_and_ticket_text_is_never_forwarded(session_and_token):
    from apps.accounts.auth.live_events import reset_snapshot

    _, token = session_and_token
    ic = _iam_client(token)
    _emit(
        "break_glass_activated",
        actor="ST-A",
        subject="ST-A",
        message="Reason: db password is hunter2 [Ticket: X]",
    )
    reset_snapshot()
    rows = ic.get("/iam/live-events/?limit=100").json()["events"]
    row = next(r for r in rows if r["event"] == "break_glass_activated")
    assert row["message"] == "" and "hunter2" not in str(rows)


def test_transaction_events_take_the_transaction_domain(db):
    from apps.accounts.auth.live_events import read_events, reset_snapshot
    from apps.accounts.models import AuthenticationTransaction as T

    for tid, dom in (("TX-IAM-1", "iam"), ("TX-WS-1", "workspace")):
        T.objects.create(
            transaction_id=tid,
            correlation_id=tid + "-c",
            domain=dom,
            stage="started",
            outcome="pending",
            flow_id="f",
            reason_category="r",
            policy_version=1,
            hasher_algorithm="argon2",
            stages=[],
        )
        AuthEvent.objects.create(
            event="auth.terminal.success", code="INFO", step="f", reference_id=tid, employee_id="X"
        )
    AuthEvent.objects.create(
        event="auth.terminal.success",
        code="INFO",
        step="f",
        reference_id="TX-GONE",
        employee_id="X",
    )
    reset_snapshot()
    by_ref = {e["reference"]: e["domain"] for e in read_events(None, "all", 50)["events"]}
    assert by_ref["TX-IAM-1"] == "iam"
    assert by_ref["TX-WS-1"] == "workspace"
    assert by_ref["TX-GONE"] == "unclassified"  # unknown transaction is never called workspace
    only_iam = read_events(None, "iam", 50)["events"]
    assert [e["reference"] for e in only_iam] == ["TX-IAM-1"]
