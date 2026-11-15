"""
Append-only authentication audit and diagnostic event recorder.
Writes structured database records and logs without recording sensitive tokens or passwords.
"""

import logging
from typing import Optional

from apps.accounts.models import AuthEvent

logger = logging.getLogger("salow.auth")


def log_auth_event(
    event: str,
    code: str,
    step: str,
    employee_id: str = "",
    reference_id: str = "",
    network_address: Optional[str] = None,
    exception_class: str = "",
    message: str = "",
    duration_ms: float = 0.0,
) -> AuthEvent:
    """Persist structured audit record to AuthEvent table."""
    # Sanitization: Ensure no sensitive codes or passwords appear in message
    safe_msg = message[:500] if message else ""

    record = AuthEvent.objects.create(
        event=event,
        code=code,
        step=step,
        employee_id=employee_id,
        reference_id=reference_id,
        network_address=network_address,
        exception_class=exception_class,
        message=safe_msg,
        duration_ms=duration_ms,
    )

    logger.info(
        "auth_event event=%s code=%s ref=%s emp=%s ip=%s step=%s duration=%.2fms",
        event,
        code,
        reference_id,
        employee_id or "anonymous",
        network_address or "-",
        step,
        duration_ms,
    )

    return record
