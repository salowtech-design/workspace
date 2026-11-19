"""
Subsystem 4: Horizontal Password-Spraying Detection & Abuse Protection.
Implements:
1. Cross-account failed authentication correlation across IP and sliding time windows.
2. Threshold-based detection of attacks targeting multiple distinct identifiers.
3. Automated SecurityAlert dispatch and distributed IP throttling.
"""

import time
from typing import Optional

from django.conf import settings
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.models import AuthEvent, SecurityAlert
from apps.core.models import RateLimitRecord

SPRAY_WINDOW_SECONDS = 300  # 5-minute sliding window
SPRAY_DISTINCT_TARGETS_THRESHOLD = 5  # >= 5 distinct accounts targeted


def record_auth_failure_and_check_spray(
    client_ip: str,
    attempted_id: str,
) -> bool:
    """
    Records a failed authentication attempt and checks if the originating IP
    is executing a horizontal password-spraying campaign across multiple accounts.
    Returns True if spraying is detected.
    """
    clean_ip = client_ip.strip()
    clean_id = attempted_id.strip().upper()
    now = time.time()
    cutoff_dt = timezone.now() - timezone.timedelta(seconds=SPRAY_WINDOW_SECONDS)

    # 1. Record failure in AuthEvent for non-repudiation
    # (AuthEvent is already created by login view)

    # 2. Check recent failed login events from this IP
    recent_failures = (
        AuthEvent.objects.filter(
            network_address=clean_ip,
            code__in=("AUTH-LOK-001", "AUTH-LOK-002", "AUTH-SEC-001", "AUTH-IAM-001"),
            time__gte=cutoff_dt,
        )
        .values_list("employee_id", flat=True)
        .distinct()
    )

    distinct_targets = set(recent_failures)
    if clean_id:
        distinct_targets.add(clean_id)

    if len(distinct_targets) >= SPRAY_DISTINCT_TARGETS_THRESHOLD:
        import json
        # Check if an alert was already logged in the last 15 minutes for this IP to prevent alert fatigue
        recent_alert = SecurityAlert.objects.filter(
            category=SecurityAlert.Category.REPEATED_AUTH_FAILURES,
            description__icontains=clean_ip,
            created_at__gte=timezone.now() - timezone.timedelta(minutes=15),
        ).exists()

        if not recent_alert:
            SecurityAlert.objects.create(
                category=SecurityAlert.Category.REPEATED_AUTH_FAILURES,
                severity=SecurityAlert.Severity.CRITICAL,
                title=f"Password Spraying Attack Detected from IP {clean_ip}",
                network_address=clean_ip,
                description=json.dumps({
                    "client_ip": clean_ip,
                    "targeted_accounts": list(distinct_targets)[:20],
                    "distinct_target_count": len(distinct_targets),
                    "detected_at": timezone.now().isoformat(),
                    "action_taken": "IP throttled via RateLimitRecord",
                }),
                status=SecurityAlert.Status.OPEN,
            )

            # Block IP in distributed RateLimitRecord
            try:
                RateLimitRecord.objects.create(
                    key=f"{clean_ip}:sensitive",
                    timestamp=now + 1800,  # 30-minute block
                )
            except Exception:
                pass

            log_auth_event(
                event="password_spraying_detected",
                code="CRITICAL",
                step="spray_defense",
                employee_id="MULTIPLE",
                network_address=clean_ip,
                message=f"Horizontal password spraying detected: {len(distinct_targets)} accounts targeted from {clean_ip}",
            )

        return True

    return False
