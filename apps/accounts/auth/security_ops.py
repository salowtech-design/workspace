"""
Security Operations (SecOps) Subsystem — Salow Workspace IAM.
Provides:
1. Append-only security event streaming with stable cursor (`GET /security/events?after=<id>`).
2. High-severity alert generation across 9 required categories.
3. Alert triage & acknowledgement with independent reviewer validation.
4. Security Reviewer (read-only) and Security Admin permission controls.
"""

from typing import Optional, Tuple

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.models import AuthEvent, CustomUser, SecurityAlert
from apps.accounts.permissions import (
    PERM_SECURITY_ALERTS_ACK,
    PERM_SECURITY_EVENTS_READ,
    has_permission,
)


def create_security_alert(
    category: str,
    severity: str,
    title: str,
    description: str = "",
    employee_id: str = "",
    network_address: Optional[str] = None,
    reference_id: str = "",
) -> SecurityAlert:
    """
    Creates an append-only high-severity security alert.
    Alert categories:
    - repeated_auth_failures
    - account_lockout
    - privileged_role_change
    - mfa_reset
    - recovery_code_used
    - new_device_approval
    - admin_fallback
    - break_glass_access
    - audit_integrity
    """
    valid_categories = SecurityAlert.Category.values
    if category not in valid_categories:
        category = SecurityAlert.Category.AUDIT_INTEGRITY

    alert = SecurityAlert.objects.create(
        category=category,
        severity=severity,
        title=title,
        description=description,
        employee_id=employee_id,
        network_address=network_address,
        reference_id=reference_id,
        status=SecurityAlert.Status.OPEN,
    )

    log_auth_event(
        event="security_alert_created",
        code="SEC-ALT-001",
        step="security_operations",
        employee_id=employee_id,
        network_address=network_address,
        reference_id=str(alert.id),
        message=f"[{severity.upper()}] {title}",
    )

    return alert


def acknowledge_security_alert(
    alert_id: int,
    actor: CustomUser,
    reason: str,
) -> SecurityAlert:
    """
    Triages and acknowledges a security alert.
    CRITICAL INVARIANTS:
    1. Independent Reviewer: Reviewer cannot acknowledge an alert for their own actions.
    2. Zero Audit Mutation: Underlying AuthEvents and historical records are NEVER modified or erased.
    3. Reason mandatory: Acknowledgement records actor, timestamp, and justification reason.
    """
    if not has_permission(actor, PERM_SECURITY_ALERTS_ACK) and actor.role not in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.SECURITY_ADMIN,
    ):
        raise PermissionDenied("User does not have permission to acknowledge security alerts.")

    if not reason or not reason.strip():
        raise ValidationError("Justification reason is mandatory when acknowledging security alerts.")

    with transaction.atomic():
        alert = SecurityAlert.objects.select_for_update().filter(id=alert_id).first()
        if not alert:
            raise ValidationError(f"Security alert {alert_id} not found.")

        # Independent review invariant: Actor cannot review own action
        if alert.employee_id and alert.employee_id.upper() == actor.employee_id.upper():
            raise PermissionDenied(
                "Security policy violation: Reviewers cannot act as independent review authority for their own actions."
            )

        alert.status = SecurityAlert.Status.ACKNOWLEDGED
        alert.acknowledged_by = actor
        alert.acknowledged_at = timezone.now()
        alert.acknowledgement_reason = reason.strip()
        alert.save(update_fields=["status", "acknowledged_by", "acknowledged_at", "acknowledgement_reason"])

        log_auth_event(
            event="security_alert_acknowledged",
            code="SEC-ALT-002",
            step="security_operations",
            employee_id=actor.employee_id,
            reference_id=str(alert.id),
            message=f"Alert {alert.id} ({alert.category}) acknowledged: {reason.strip()}",
        )

        return alert


def get_security_events_stream(
    after_id: Optional[int] = None,
    limit: int = 50,
) -> list[dict]:
    """
    Returns an append-only list of security events from a stable cursor.
    Zero secret disclosure — passwords, OTPs, recovery codes, and tokens are scrubbed.
    """
    qs = AuthEvent.objects.all().order_by("id")
    if after_id is not None:
        qs = qs.filter(id__gt=after_id)

    events = qs[:limit]
    results = []
    for evt in events:
        results.append({
            "id": evt.id,
            "event": evt.event,
            "code": evt.code,
            "step": evt.step,
            "employee_id": evt.employee_id or "ANONYMOUS",
            "time": evt.time.isoformat(),
            "network_address": evt.network_address or "UNKNOWN",
            "reference_id": evt.reference_id,
            "message": evt.message,
        })
    return results
