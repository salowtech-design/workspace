"""Server-sent events (SSE) push for the IAM live action feed. Needs an ASGI server.

Fallback: under WSGI (the current gunicorn sync workers) the stream endpoint answers 404 and
the page keeps using the one-second JSON poll in live_events.py. The same events and the same
safe payload fields are used; this module only changes how they are delivered.

Per worker process, one broadcaster task reads AuthEvent rows with id > the last seen id every
BROADCAST_INTERVAL seconds and wakes every connection. So database load is one query per
interval per process, not one per connection.

Each connection is rechecked against the database every RECHECK_INTERVAL seconds: the IAM
session must still be active, not idle or past its absolute limit, the administrator and the
linked employee must still be active, and the administrator role must be unchanged. If not,
the stream is closed with a final `end` event and no more events are delivered. So the longest
time a revoked or demoted session can still receive an event is RECHECK_INTERVAL plus one
database round trip. Connections are also closed after MAX_LIFETIME seconds; the browser
reconnects with Last-Event-ID and the missed events are replayed from the database.
"""

import asyncio
import json
import time
from collections import deque
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.accounts.auth.live_events import public_payload, read_events, transaction_domains
from apps.accounts.models import AuthEvent

BROADCAST_INTERVAL = 0.25
RECHECK_INTERVAL = 5.0
HEARTBEAT_INTERVAL = 15.0
MAX_LIFETIME = 300.0
MAX_CONNECTIONS_PER_PROCESS = 200
MAX_CONNECTIONS_PER_SESSION = 3
BUFFER_SIZE = 500
OVERLAP = 20  # re-read the last few ids so a row that committed late is not skipped

_state = {
    "loop": None,
    "task": None,
    "buffer": deque(maxlen=BUFFER_SIZE),
    "last_id": 0,
    "ready": False,
}
_subscribers = set()
_per_session = {}
_roles = {}  # session_id -> role at connect
_problems = {}  # session_id -> reason, refreshed every broadcast tick


def _problem_of(s, role: str):
    """Why a loaded IAMSession row must stop streaming, or None."""
    from apps.accounts.auth.iam_auth import (
        IAM_SESSION_ABSOLUTE_TIMEOUT_HOURS,
        effective_iam_idle_timeout_minutes,
    )
    from apps.accounts.models import CustomUser, IAMAdminIdentity

    if s is None or not s.is_active:
        return "session_ended"
    now = timezone.now()
    if s.last_activity and now - s.last_activity > timedelta(
        minutes=effective_iam_idle_timeout_minutes()
    ):
        return "idle_timeout"
    if s.created_at and now - s.created_at > timedelta(hours=IAM_SESSION_ABSOLUTE_TIMEOUT_HOURS):
        return "session_expired"
    a = s.iam_admin
    if (
        a.status != IAMAdminIdentity.Status.ACTIVE
        or a.user.status != CustomUser.Status.ACTIVE
        or not a.user.is_active
    ):
        return "account_inactive"
    if a.role != role:
        return "role_changed"
    return None


def _load_sessions(ids):
    from apps.accounts.models import IAMSession

    qs = IAMSession.objects.select_related("iam_admin", "iam_admin__user").filter(pk__in=ids)
    return {s.pk: s for s in qs}


def session_problem(session_id: int, role: str):
    """Return why the stream must stop, or None. Reads the database every call (no cache)."""
    return _problem_of(_load_sessions([session_id]).get(session_id), role)


def _check_all(roles: dict) -> dict:
    """One query for every open stream in this process: {session_id: reason or None}."""
    loaded = _load_sessions(list(roles))
    return {sid: _problem_of(loaded.get(sid), role) for sid, role in roles.items()}


def _fetch_after(last_id: int):
    rows = AuthEvent.objects.filter(id__gt=max(0, last_id - OVERLAP)).order_by("id")[:500]
    return list(rows)


async def _broadcast_loop():
    try:
        if _state["last_id"] == 0:
            top = await sync_to_async(lambda: AuthEvent.objects.order_by("-id").first())()
            _state["last_id"] = top.id if top else 0
        _state["ready"] = True
        seen = deque(maxlen=2000)
        while _subscribers:
            rows = await sync_to_async(_fetch_after)(_state["last_id"])
            tx = await sync_to_async(transaction_domains)(rows)
            for r in rows:
                if r.id in seen:
                    continue
                seen.append(r.id)
                payload = public_payload(r, tx.get(r.reference_id))
                payload["_t"] = time.time()
                _state["buffer"].append(payload)
                _state["last_id"] = max(_state["last_id"], r.id)
            # Authorization for every open stream is re-read on each tick, BEFORE waking the
            # connections, so a revoked or demoted session is stopped before this tick's events.
            if _roles:
                _problems.clear()
                _problems.update(await sync_to_async(_check_all)(dict(_roles)))
            for q in list(_subscribers):
                q.set()
            await asyncio.sleep(BROADCAST_INTERVAL)
    finally:
        _state["task"] = None
        _state["ready"] = False
        _state["last_id"] = 0
        _state["buffer"].clear()


def _ensure_broadcaster():
    loop = asyncio.get_running_loop()
    if _state["task"] is None or _state["loop"] is not loop:
        _state["loop"] = loop
        _state["task"] = loop.create_task(_broadcast_loop())


def sse_frame(payload: dict) -> bytes:
    body = {k: v for k, v in payload.items() if not k.startswith("_")}
    return f"id: {body['id']}\nevent: audit\ndata: {json.dumps(body)}\n\n".encode()


def connection_counts() -> dict:
    return {"connections": len(_subscribers), "sessions": len(_per_session)}


def admit(session_id: int) -> bool:
    if len(_subscribers) >= MAX_CONNECTIONS_PER_PROCESS:
        return False
    return _per_session.get(session_id, 0) < MAX_CONNECTIONS_PER_SESSION


async def _wait(wake) -> None:
    try:
        await asyncio.wait_for(wake.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pass
    wake.clear()


def _release(wake, session_id: int) -> None:
    _subscribers.discard(wake)
    n = _per_session.get(session_id, 1) - 1
    if n <= 0:
        _per_session.pop(session_id, None)
        _roles.pop(session_id, None)
        _problems.pop(session_id, None)
    else:
        _per_session[session_id] = n


async def _replay(after_id, domain):
    """Events from the database after `after_id` (or the latest 20), as a list of payloads."""
    out = []
    replay_after = after_id
    for _ in range(10):
        page = await sync_to_async(read_events)(
            replay_after, domain, 20 if replay_after is None else 100
        )
        out.extend(page["events"])
        if replay_after is None or len(page["events"]) < 100:
            break
        replay_after = page["cursor"]
    return out


def _new_frames(floor: int, sent: set, domain: str) -> list:
    """Frames for buffered events newer than the floor and not yet sent on this connection."""
    frames = []
    batch = [p for p in list(_state["buffer"]) if p["id"] > floor and p["id"] not in sent]
    for p in sorted(batch, key=lambda x: x["id"]):
        sent.add(p["id"])
        if domain in ("iam", "workspace", "unclassified") and p["domain"] != domain:
            continue
        frames.append(sse_frame(p))
    if len(sent) > 4000:
        keep = sorted(sent)[-1000:]
        sent.clear()
        sent.update(keep)
    return frames


def _end_frame(reason: str) -> bytes:
    return f"event: end\ndata: {json.dumps({'reason': reason})}\n\n".encode()


async def stream(session_id: int, role: str, domain: str, after_id):
    """Async generator of SSE bytes for one connection."""
    wake = asyncio.Event()
    _subscribers.add(wake)
    _per_session[session_id] = _per_session.get(session_id, 0) + 1
    _roles[session_id] = role
    started = last_check = last_send = time.monotonic()
    try:
        _ensure_broadcaster()
        yield b"retry: 3000\n\n"
        while not _state["ready"]:
            await asyncio.sleep(0.02)
        floor = after_id if after_id is not None else _state["last_id"]
        sent = set()
        for e in await _replay(after_id, domain):
            sent.add(e["id"])
            yield sse_frame(e)
        yield b": connected\n\n"
        while True:
            await _wait(wake)
            now = time.monotonic()
            reason = None
            if _problems.get(session_id):
                reason = _problems[session_id]
            elif now - started >= MAX_LIFETIME:
                reason = "reconnect"
            elif now - last_check >= RECHECK_INTERVAL:
                last_check = now
                reason = await sync_to_async(session_problem)(session_id, role)
            if reason:
                yield _end_frame(reason)
                return
            for frame in _new_frames(floor, sent, domain):
                yield frame
                last_send = time.monotonic()
            if time.monotonic() - last_send >= HEARTBEAT_INTERVAL:
                yield b": keepalive\n\n"
                last_send = time.monotonic()
    finally:
        _release(wake, session_id)
