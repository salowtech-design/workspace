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

# IAM-console administration, privileged access, recovery and security operations.
# Everything else (sign-ins, password and TOTP checks, device approvals by users) is workspace activity.
_IAM_PREFIXES = (
    "iam_",
    "iam.",
    "break_glass",
    "emergency_access",
    "service_identity",
    "mfa_reset",
    "mfa_replacement_token",
    "admin_recovery",
    "recovery_code_issued",
    "security_incident",
    "security_alert",
    "access_review",
    "backup_",
    "bootstrap_admin",
    "auth.policy",
    "auth.session.revoked",
    "auth.lock",
    "auth.identity",
    "auth.device.revoked",
    "employee_",
    "account_",
    "audit.records",
)
_IAM_EVENTS = {
    "role_updated_by_admin",
    "team_membership_added",
    "outbox_email_requeued",
    "outbox_event_requeued",
    "sessions_revoked",
    "device_approved_by_admin",
}
# Only these events show their free-text message. Their messages are written by the
# application from ids and role names. Every other event shows its name only: truncation
# is not redaction, so unknown free text is not forwarded.
_MESSAGE_SAFE = {
    "team_membership_added",
    "outbox_email_requeued",
    "outbox_event_requeued",
    "role_updated_by_admin",
    "employee_created_by_admin",
    "account_suspended_by_admin",
    "account_exited_by_admin",
    "account_reactivated_by_admin",
    "break_glass_deactivated",
    "sessions_revoked",
}

_lock = threading.Lock()
_snapshot = {"at": 0.0, "events": []}


_WORKSPACE_PREFIXES = (
    "totp_",
    "password_",
    "login_",
    "session_",
    "second_device",
    "sudo_",
    "workspace_",
    "activation_",
    "passkey_",
    "device_",
    "email_",
    "mfa_replacement",
    "account_locked",
    "elevated_risk",
    "risk_policy",
    "role_marker",
    "service_auth",
    "code_",
    "recovery_code",
    "auth.requested",
    "auth.terminal",
    "auth.risk",
    "auth.session_decision",
    "auth.mfa",
    "auth.device",
    "backup_",
    "security.",
    "alert.",
    "biometric_",
    "passkey",
)


_NOT_TRANSACTION = (
    "auth.policy",
    "auth.session.revoked",
    "auth.lock",
    "auth.identity",
    "auth.device.revoked",
)


def is_transaction_event(event: str) -> bool:
    """auth.requested, auth.terminal.*, auth.<stage>.<result>: written by the authentication
    transaction recorder with reference_id = the transaction id. Their domain is the
    transaction's own domain (IAM or workspace), not a guess from the event name."""
    return event.startswith("auth.") and not event.startswith(_NOT_TRANSACTION)


def domain_of(event: str, tx_domain=None) -> str:
    """iam, workspace, or unclassified. A new event name is never silently called workspace.
    Transaction events take the domain recorded on their authentication transaction."""
    if is_transaction_event(event):
        return tx_domain if tx_domain in ("iam", "workspace") else "unclassified"
    if event in _IAM_EVENTS or event.startswith(_IAM_PREFIXES):
        return "iam"
    if event.startswith(_WORKSPACE_PREFIXES):
        return "workspace"
    return "unclassified"


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


def _safe_message(evt: AuthEvent) -> str:
    """Free text is shown only for allow-listed events whose message is built from ids and
    role names. Events that put request-derived text (break-glass reason and ticket) in the
    message are not on the list. Whitespace is collapsed so the text is one line."""
    if evt.event not in _MESSAGE_SAFE:
        return ""
    return " ".join((evt.message or "").split())[:200]


def transaction_domains(events) -> dict:
    """{transaction id: 'iam'|'workspace'} for the transaction events in `events` (one query)."""
    from apps.accounts.models import AuthenticationTransaction

    ids = {e.reference_id for e in events if is_transaction_event(e.event) and e.reference_id}
    if not ids:
        return {}
    rows = AuthenticationTransaction.objects.filter(transaction_id__in=ids).values_list(
        "transaction_id", "domain"
    )
    return dict(rows)


# Events written by the sign-in paths that carry only the submitted identity.
SIGNIN_EVENTS = frozenset(
    {
        "password_verified",
        "totp_verified",
        "totp_invalid",
        "totp_replay_rejected",
        "session_established",
        "second_device_session_established",
        "activation_failed",
        "recovery_code_failed",
        "passkey_auth_failed",
    }
)
# Only these mean the identity was authenticated, so only these name it as the actor.
VERIFIED_SIGNIN_EVENTS = frozenset({"auth.terminal.success", "session_established"})


def public_payload(evt: AuthEvent, tx_domain=None) -> dict:
    """Only these fields leave the server. No network address, no exception text."""
    domain = domain_of(evt.event, tx_domain)
    event_name = evt.event or ""
    subject = evt.employee_id or ""
    is_signin = event_name.startswith(("auth.", "login_failed")) or event_name in SIGNIN_EVENTS
    # A recorded actor is always shown as recorded. A sign-in row carries only the identity
    # that was submitted: it is the actor only once authentication succeeded. Before that
    # (wrong password, wrong code, unknown ID, someone else's ID) it is shown as an
    # unverified claim and never as the actor.
    if evt.actor_employee_id:
        actor, state = evt.actor_employee_id, "recorded"
    elif event_name in VERIFIED_SIGNIN_EVENTS and subject:
        actor, state = subject, "verified"
    elif is_signin:
        actor, state = "", ("claimed" if subject else "none")
    else:
        actor = subject if domain == "workspace" else ""
        state = "recorded" if actor else "none"
    return {
        "id": evt.id,
        "time": evt.time.isoformat(),
        "domain": domain,
        "event": evt.event,
        "code": evt.code,
        "actor": actor or "",
        "actor_state": state,
        "claimed_identity": subject if state == "claimed" else "",
        "subject": evt.employee_id or "",
        "reference": (evt.reference_id or "")[:64],
        "message": _safe_message(evt),
    }


def read_events(after_id, domain: str = "all", limit: int = 50) -> dict:
    """Events with id > after_id (oldest first). Without a cursor: the latest `limit`."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    events = _refresh(time.monotonic())
    if after_id is not None and events and after_id < events[0].id - 1:
        # The cursor is older than the snapshot window: read the gap from the database.
        events = list(AuthEvent.objects.filter(id__gt=after_id).order_by("id")[:limit])
    tx = transaction_domains(events)

    def dom(e):
        return domain_of(e.event, tx.get(e.reference_id))

    if domain in ("iam", "workspace", "unclassified"):
        events = [e for e in events if dom(e) == domain]
    if after_id is None:
        picked = events[-limit:]
    else:
        picked = [e for e in events if e.id > after_id][:limit]
    cursor = picked[-1].id if picked else (after_id or 0)
    return {
        "events": [public_payload(e, tx.get(e.reference_id)) for e in picked],
        "cursor": cursor,
    }
