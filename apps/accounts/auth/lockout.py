"""
Database-Backed Lockout Counter Engine.
Survives across worker process boundaries and enforces 15-minute cool-off.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import (
    LOCKOUT_DURATION_MINUTES,
    LOCKOUT_THRESHOLD,
)
from apps.accounts.auth.errors import AuthException
from apps.accounts.models import LoginAttempt


def get_attempt_record(key: str) -> LoginAttempt:
    """Retrieve or create DB counter record."""
    record, _ = LoginAttempt.objects.get_or_create(key=key)
    return record


def check_lockout(key: str, employee_id: str = "") -> None:
    """Check if key is currently locked out. Resets counter if cool-off has elapsed."""
    record = LoginAttempt.objects.filter(key=key).first()
    if not record or not record.locked_until:
        return

    now = timezone.now()
    if now < record.locked_until:
        remaining_seconds = int((record.locked_until - now).total_seconds())
        remaining_mins = max(1, remaining_seconds // 60)
        log_auth_event(
            event="account_locked_attempt",
            code="AUTH-LOG-002",
            step="check_lockout",
            employee_id=employee_id,
            message=f"Attempt while locked ({remaining_mins} mins remaining)",
        )
        raise AuthException(
            "AUTH-LOG-002",
            step="check_lockout",
            employee_id=employee_id,
            message_override=(
                f"Account is locked due to consecutive failures. "
                f"Try again in {remaining_mins} minute(s)."
            ),
        )

    # Cool-off period has passed, reset counter
    record.counter = 0
    record.locked_until = None
    record.save(update_fields=["counter", "locked_until", "updated_at"])


def record_failed_attempt(key: str, employee_id: str = "") -> int:
    """Increment failure counter and engage lock if threshold reached."""
    with transaction.atomic():
        record, _ = LoginAttempt.objects.select_for_update().get_or_create(key=key)
        record.counter += 1

        if record.counter >= LOCKOUT_THRESHOLD:
            record.locked_until = timezone.now() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
            log_auth_event(
                event="account_locked",
                code="AUTH-LOG-002",
                step="record_failure",
                employee_id=employee_id,
                message=(
                    f"Locked for {LOCKOUT_DURATION_MINUTES} minutes "
                    f"after {record.counter} failed attempts"
                ),
            )
        record.save(update_fields=["counter", "locked_until", "updated_at"])
        return record.counter


def clear_failed_attempts(key: str) -> None:
    """Clear attempt counter on successful authentication."""
    LoginAttempt.objects.filter(key=key).update(counter=0, locked_until=None)
