"""
Append-only authentication audit and diagnostic event recorder.
Writes structured database records and logs without recording sensitive tokens or passwords.
"""

import logging
from typing import Optional

from apps.accounts.models import AuthEvent

logger = logging.getLogger("salow.auth")


def _resolve_alert_details(event: str, code: str, employee_id: str, message: str):
    from apps.accounts.models import SecurityAlert

    if event == "account_locked":
        return (
            SecurityAlert.Category.ACCOUNT_LOCKOUT,
            SecurityAlert.Severity.HIGH,
            f"Account locked for {employee_id or 'unknown user'}",
        )
    if event == "login_failed_wrong_password" and code == "AUTH-LOG-002":
        return (
            SecurityAlert.Category.REPEATED_AUTH_FAILURES,
            SecurityAlert.Severity.HIGH,
            f"Repeated login failures on account {employee_id}",
        )
    if event in ("role_updated_by_admin", "employee_created_by_admin") and any(
        kw in message.lower() for kw in ("admin", "super_admin", "security_admin")
    ):
        return (
            SecurityAlert.Category.PRIVILEGED_ROLE_CHANGE,
            SecurityAlert.Severity.HIGH,
            f"Privileged role assignment: {message[:120]}",
        )
    if event in ("mfa_reset_code_issued", "mfa_replaced_successfully"):
        return (
            SecurityAlert.Category.MFA_RESET,
            SecurityAlert.Severity.HIGH,
            f"MFA reset action for {employee_id}",
        )
    if event == "recovery_code_redeemed":
        return (
            SecurityAlert.Category.RECOVERY_CODE_USED,
            SecurityAlert.Severity.HIGH,
            f"Emergency recovery code used by {employee_id}",
        )
    if event == "device_approved_by_user":
        return (
            SecurityAlert.Category.NEW_DEVICE_APPROVAL,
            SecurityAlert.Severity.MEDIUM,
            f"New secondary device approved for {employee_id}",
        )
    if event == "device_approved_by_admin":
        return (
            SecurityAlert.Category.ADMIN_FALLBACK,
            SecurityAlert.Severity.HIGH,
            f"Secondary device approved via admin fallback for {employee_id}",
        )
    if event == "break_glass_activated":
        return (
            SecurityAlert.Category.BREAK_GLASS_ACCESS,
            SecurityAlert.Severity.CRITICAL,
            f"CRITICAL: Break-glass emergency access activated by {employee_id}",
        )
    if event in ("role_marker_mismatch", "certificate_tampered", "audit_integrity_violation"):
        return (
            SecurityAlert.Category.AUDIT_INTEGRITY,
            SecurityAlert.Severity.HIGH,
            f"Integrity incident detected: {message[:120]}",
        )
    return None, None, ""


def _dispatch_security_alert_for_event(
    event: str,
    code: str,
    employee_id: str,
    network_address: Optional[str],
    reference_id: str,
    message: str,
) -> None:
    """Dispatches high-severity alerts to SecOps for required categories (Section 13)."""
    try:
        from apps.accounts.models import SecurityAlert
    except ImportError:
        return

    category, severity, title = _resolve_alert_details(event, code, employee_id, message)
    title = title[:200]
    employee_id = (employee_id or "")[:32]
    reference_id = (reference_id or "")[:64]
    if category:
        try:
            SecurityAlert.objects.create(
                category=category,
                severity=severity,
                title=title,
                description=message,
                employee_id=employee_id,
                network_address=network_address,
                reference_id=reference_id,
                status=SecurityAlert.Status.OPEN,
            )
        except Exception as exc:
            logger.debug("Failed to record SecOps alert: %s", exc)


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
    # Attacker-controlled identifiers must fit the columns; strict MySQL/TiDB raises
    # DataError (a 500) on overflow where SQLite silently accepts it.
    code = (code or "")[:32]
    step = (step or "")[:64]
    event = (event or "")[:64]
    employee_id = (employee_id or "")[:32]
    reference_id = (reference_id or "")[:64]
    exception_class = (exception_class or "")[:128]

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

    # Automatic High-Severity Security Alert Dispatch (Section 13)
    if event not in ("security_alert_created", "security_alert_acknowledged"):
        _dispatch_security_alert_for_event(
            event=event,
            code=code,
            employee_id=employee_id,
            network_address=network_address,
            reference_id=reference_id,
            message=safe_msg,
        )

    return record
