"""
Subsystem 2: Security Alert Delivery & Escalation Engine.
Implements:
1. Durable alert dispatch via transactional outbox (EmailOutbox and OutboxEvent).
2. Time-based SLA tracking with automatic escalation after 15 minutes of unacknowledged alerts.
3. Severity elevation to CRITICAL for breached SLAs.
"""

from datetime import timedelta
from typing import List

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.models import CustomUser, SecurityAlert
from apps.core.models import EmailOutbox
from apps.core.outbox import publish_event

ALERT_SLA_MINUTES = 15


def escalate_unacknowledged_alerts() -> List[SecurityAlert]:
    """
    Scans for open security alerts exceeding the 15-minute SLA.
    Elevates severity to CRITICAL, increments escalation tracking,
    and dispatches high-priority outbox notifications.
    """
    cutoff = timezone.now() - timedelta(minutes=ALERT_SLA_MINUTES)
    escalated = []

    # Find open alerts created before cutoff that have not yet been acknowledged
    with transaction.atomic():
        stale_alerts = (
            SecurityAlert.objects.select_for_update()
            .filter(
                status=SecurityAlert.Status.OPEN,
                created_at__lte=cutoff,
            )
            .order_by("created_at")
        )

        for alert in stale_alerts:
            desc = alert.description or ""
            if "SLA Breach" in desc:
                continue

            alert.severity = SecurityAlert.Severity.CRITICAL
            alert.description = f"{desc}\n[ESCALATED {timezone.now().isoformat()}]: SLA Breach: Unacknowledged for > {ALERT_SLA_MINUTES} minutes".strip()
            alert.save(update_fields=["severity", "description"])

            # 1. Enqueue outbox notification to designated security reviewers
            reviewer_emails = list(
                CustomUser.objects.filter(
                    role__in=(CustomUser.Role.SECURITY_REVIEWER, CustomUser.Role.SECURITY_ADMIN, CustomUser.Role.SUPER_ADMIN),
                    status=CustomUser.Status.ACTIVE,
                ).values_list("email", flat=True)
            )

            for email in reviewer_emails:
                if email:
                    EmailOutbox.objects.create(
                        recipient=email,
                        subject=f"[ESCALATION - CRITICAL] Security Alert: {alert.title}",
                        body_text=(
                            f"SECURITY ALERT ESCALATION\n\n"
                            f"Alert ID: {alert.id}\n"
                            f"Category: {alert.category}\n"
                            f"Severity: CRITICAL (Escalated after 15 minutes unacknowledged)\n"
                            f"Created At: {alert.created_at.isoformat()}\n\n"
                            f"Details: {alert.description}\n"
                        ),
                        status=EmailOutbox.Status.QUEUED,
                    )

            # 2. Emit durable domain outbox event
            publish_event(
                event_type="security_alert.escalated",
                payload={
                    "alert_id": alert.id,
                    "category": alert.category,
                    "severity": alert.severity,
                    "title": alert.title,
                },
                event_id=f"esc-alert-{alert.id}-{int(timezone.now().timestamp())}",
            )

            log_auth_event(
                event="security_alert_escalated",
                code="CRITICAL",
                step="alert_escalation",
                employee_id="SYSTEM",
                reference_id=str(alert.id),
                message=f"Alert [{alert.id}] escalated to CRITICAL after 15 minutes unacknowledged",
            )
            escalated.append(alert)

    return escalated
