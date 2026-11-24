"""Cursor-based live action feed over AuthEvent, with a shared per-process snapshot.

Why a snapshot: many dashboards polling once a second would each run a query.
One process refreshes its snapshot at most every SNAPSHOT_TTL seconds and every
poll in that process reads from it. Authorization is not cached; the view checks
the IAM session on every request, so a revoked session stops receiving events at
its next poll. This is a polling feed, not a persistent stream: it holds no worker
thread between polls, so it works with the current sync gunicorn workers.
"""

import threading
import time

from apps.accounts.models import AuthEvent

SNAPSHOT_TTL = 0.5
SNAPSHOT_SIZE = 300
MAX_LIMIT = 100

# Events produced by IAM-console administration. Everything else is workspace activity.
_IAM_PREFIXES = ("iam_", "break_glass", "service_identity", "mfa_reset", "recovery_")
_IAM_EVENTS = {
    "employee_created_by_admin",
    "role_updated_by_admin",
    "account_suspended_by_admin",
    "account_exited_by_admin",
    "account_reactivated_by_admin",
    "team_membership_added",
    "outbox_email_requeued",
    "outbox_event_requeued",
    "sessions_revoked",
}

_lock = threading.Lock()
_snapshot = {"at": 0.0, "events": []}


def domain_of(event: str) -> str:
    if event in _IAM_EVENTS or event.startswith(_IAM_PREFIXES):
        return "iam"
    return "workspace"


def _refresh(now: float) -> list:
    with _lock:
        if now - _snapshot["at"] >= SNAPSHOT_TTL:
            rows = list(AuthEvent.objects.order_by("-id")[:SNAPSHOT_SIZE])
            _snapshot["events"] = list(reversed(rows))
            _snapshot["at"] = now
        return _snapshot["events"]


def reset_snapshot() -> None:
    with _lock:
        _snapshot["at"] = 0.0
        _snapshot["events"] = []


def public_payload(evt: AuthEvent) -> dict:
    """Only these fields leave the server. No network address, no exception text."""
    return {
        "id": evt.id,
        "time": evt.time.isoformat(),
        "domain": domain_of(evt.event),
        "event": evt.event,
        "code": evt.code,
        "actor": evt.actor_employee_id or "",
        "subject": evt.employee_id or "",
        "reference": evt.reference_id or "",
        "message": (evt.message or "")[:200],
    }


def read_events(after_id, domain: str = "all", limit: int = 50) -> dict:
    """Events with id > after_id (oldest first). Without a cursor: the latest `limit`."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    events = _refresh(time.monotonic())
    if after_id is not None and events and after_id < events[0].id - 1:
        # The cursor is older than the snapshot window: read the gap from the database.
        events = list(AuthEvent.objects.filter(id__gt=after_id).order_by("id")[:limit])
    if domain in ("iam", "workspace"):
        events = [e for e in events if domain_of(e.event) == domain]
    if after_id is None:
        picked = events[-limit:]
    else:
        picked = [e for e in events if e.id > after_id][:limit]
    cursor = picked[-1].id if picked else (after_id or 0)
    return {"events": [public_payload(e) for e in picked], "cursor": cursor}
