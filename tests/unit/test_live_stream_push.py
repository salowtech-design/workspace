"""SSE push for the IAM live feed: checks on an ACTIVE stream, not only on a new request."""

import asyncio
import json
import time
from datetime import timedelta

import pytest
from django.contrib.auth.hashers import make_password
from django.test import AsyncRequestFactory, RequestFactory
from django.utils import timezone

from apps.accounts.auth import live_stream
from apps.accounts.auth.iam_auth import (
    IAM_SESSION_COOKIE_NAME,
    create_iam_admin_identity,
    create_iam_session,
)
from apps.accounts.models import AuthEvent, CustomUser, IAMAdminIdentity, IAMSession
from apps.accounts.views.authentication_centre import iam_live_stream_view


@pytest.fixture
def admin_session(db):
    user = CustomUser.objects.create(
        email="push.admin@salowtech.com",
        employee_id="EMP-PUSH-01",
        full_name="Push Admin",
        password=make_password("SuperSecureIamPass2026!"),
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    admin, _ = create_iam_admin_identity(
        user=user,
        admin_id="IAM-PUSH-01",
        password="IamMasterSecretPassword2026!#",
        role=IAMAdminIdentity.Role.SUPER_ADMIN,
    )
    setup = RequestFactory().get("/")
    session = create_iam_session(admin, setup)
    return session, setup.session_key_iam, admin


@pytest.fixture(autouse=True)
def fast_stream(monkeypatch):
    monkeypatch.setattr(live_stream, "BROADCAST_INTERVAL", 0.05)
    monkeypatch.setattr(live_stream, "RECHECK_INTERVAL", 30.0)
    live_stream._state.update({"loop": None, "task": None, "last_id": 0, "ready": False})
    live_stream._state["buffer"].clear()
    live_stream._subscribers.clear()
    live_stream._roles.clear()
    live_stream._problems.clear()
    live_stream._per_session.clear()


def _mk(event, message="", employee_id="EMP-X"):
    return AuthEvent.objects.create(
        event=event, code="INFO", step="t", employee_id=employee_id, message=message
    )


async def _collect(gen, seconds):
    out = []

    async def run():
        async for chunk in gen:
            out.append(chunk.decode())

    try:
        await asyncio.wait_for(run(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
    return "".join(out)


def test_wsgi_request_gets_404_so_page_keeps_polling(admin_session):
    _, token, _ = admin_session
    request = RequestFactory().get("/iam/live-stream/")
    request.COOKIES[IAM_SESSION_COOKIE_NAME] = token
    # A plain WSGI request has no ASGI scope; the async view refuses to hold a worker.
    from django.http import Http404

    with pytest.raises(Http404):
        asyncio.run(iam_live_stream_view(request))


@pytest.mark.django_db(transaction=True)
def test_no_session_is_401():
    request = AsyncRequestFactory().get("/iam/live-stream/")
    response = asyncio.run(iam_live_stream_view(request))
    assert response.status_code == 401


@pytest.mark.django_db(transaction=True)
def test_active_stream_delivers_new_event_and_no_secret(admin_session):
    session, _, admin = admin_session

    async def scenario():
        async def writer():
            await asyncio.sleep(0.6)
            from asgiref.sync import sync_to_async

            await sync_to_async(_mk)("password_verified", "token=SECRET-VALUE-123")
            await sync_to_async(_mk)("team_membership_added", "Added to team ENG")

        w = asyncio.create_task(writer())
        text = await _collect(live_stream.stream(session.pk, admin.role, "all", None), 2.0)
        await w
        return text

    text = asyncio.run(scenario())
    assert "password_verified" in text
    assert "team_membership_added" in text
    assert "SECRET-VALUE-123" not in text  # not on the message allow-list
    assert "Added to team ENG" in text  # on the allow-list


@pytest.mark.django_db(transaction=True)
def test_revocation_closes_an_active_stream_within_window(admin_session):
    session, _, admin = admin_session

    async def scenario():
        from asgiref.sync import sync_to_async

        async def revoke_then_write():
            await asyncio.sleep(0.5)
            await sync_to_async(
                lambda: IAMSession.objects.filter(pk=session.pk).update(is_active=False)
            )()
            # The window is one tick. Even with the 5 s recheck disabled (30 s here), an event
            # written right after revocation, once a tick has passed, must not be delivered.
            await asyncio.sleep(0.4)
            await sync_to_async(_mk)("team_membership_added", "after revoke")

        t = asyncio.create_task(revoke_then_write())
        t0 = time.monotonic()
        text = await _collect(live_stream.stream(session.pk, admin.role, "all", None), 3.0)
        await t
        return text, time.monotonic() - t0

    text, took = asyncio.run(scenario())
    assert "event: end" in text and "session_ended" in text
    assert "after revoke" not in text
    # Window: one broadcast tick (0.05 s here, 0.25 s in production) plus a database round trip.
    assert took < 2.0


@pytest.mark.django_db(transaction=True)
def test_role_change_closes_an_active_stream(admin_session):
    session, _, admin = admin_session

    async def scenario():
        from asgiref.sync import sync_to_async

        async def demote():
            await asyncio.sleep(0.5)
            await sync_to_async(
                lambda: IAMAdminIdentity.objects.filter(pk=admin.pk).update(
                    role=IAMAdminIdentity.Role.SECURITY_REVIEWER
                )
            )()

        t = asyncio.create_task(demote())
        text = await _collect(live_stream.stream(session.pk, admin.role, "all", None), 3.0)
        await t
        return text

    assert "role_changed" in asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_suspended_employee_and_idle_close_stream(admin_session):
    session, _, admin = admin_session
    CustomUser.objects.filter(pk=admin.user_id).update(status=CustomUser.Status.SUSPENDED)
    assert live_stream.session_problem(session.pk, admin.role) == "account_inactive"
    CustomUser.objects.filter(pk=admin.user_id).update(status=CustomUser.Status.ACTIVE)
    IAMSession.objects.filter(pk=session.pk).update(
        last_activity=timezone.now() - timedelta(hours=1)
    )
    assert live_stream.session_problem(session.pk, admin.role) == "idle_timeout"


@pytest.mark.django_db(transaction=True)
def test_replay_from_last_event_id_and_domain_filter(admin_session):
    session, _, admin = admin_session
    first = _mk("team_membership_added", "Added to team A")
    _mk("password_verified", "x")
    _mk("team_membership_added", "Added to team B")

    async def scenario():
        return await _collect(live_stream.stream(session.pk, admin.role, "iam", first.id), 1.0)

    text = asyncio.run(scenario())
    assert "Added to team B" in text
    assert "Added to team A" not in text  # at or before the cursor
    assert "password_verified" not in text  # workspace domain filtered out
    frames = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]
    assert all(f["domain"] == "iam" for f in frames)


@pytest.mark.django_db(transaction=True)
def test_connection_caps(admin_session, monkeypatch):
    session, _, _ = admin_session
    monkeypatch.setattr(live_stream, "MAX_CONNECTIONS_PER_SESSION", 2)
    live_stream._per_session[session.pk] = 2
    assert live_stream.admit(session.pk) is False
    live_stream._per_session[session.pk] = 1
    assert live_stream.admit(session.pk) is True
