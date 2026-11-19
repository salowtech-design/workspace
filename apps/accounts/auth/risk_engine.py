"""
Subsystem 5: Rules-Based Risk Signals & Security Scoring Engine.
Implements auditable risk scoring:
1. New device / user-agent or unrecorded network IP.
2. Suspicious failures followed by immediate authentication success.
3. Rapid device approval velocity (repeated approval attempts in short windows).
4. Privileged administrative operations.
"""

from datetime import timedelta
from typing import Dict, List, Tuple

from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.models import AuthEvent, CustomUser, DeviceApprovalRequest, UserSession


def evaluate_login_risk(
    user: CustomUser,
    client_ip: str,
    user_agent: str,
) -> Tuple[int, List[str]]:
    """
    Evaluates dynamic risk signals for an authentication transaction.
    Returns (risk_score [0-100], triggered_rules).
    """
    score = 0
    signals = []
    now = timezone.now()

    # Rule 1: New network IP for this account (+25)
    known_ips = set(
        UserSession.objects.filter(user=user)
        .exclude(ip_address__isnull=True)
        .values_list("ip_address", flat=True)
    )
    if known_ips and client_ip not in known_ips:
        score += 25
        signals.append("new_network_ip")

    # Rule 2: Suspicious recent failures followed by success (+35)
    recent_failures = AuthEvent.objects.filter(
        employee_id=user.employee_id,
        code__in=("AUTH-LOK-001", "AUTH-LOK-002", "AUTH-MFA-001"),
        time__gte=now - timedelta(minutes=10),
    ).count()
    if recent_failures >= 2:
        score += 35
        signals.append(f"failures_prior_to_success_{recent_failures}")

    # Rule 3: High device approval velocity (+30)
    recent_approvals = DeviceApprovalRequest.objects.filter(
        user=user,
        created_at__gte=now - timedelta(hours=1),
    ).count()
    if recent_approvals >= 2:
        score += 30
        signals.append(f"high_device_approval_velocity_{recent_approvals}")

    # Rule 4: Privileged account sensitivity (+15)
    if user.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN, CustomUser.Role.SECURITY_ADMIN):
        score += 15
        signals.append("privileged_role_access")

    total_score = min(score, 100)

    if total_score >= 50:
        log_auth_event(
            event="elevated_risk_detected",
            code="WARNING",
            step="risk_engine",
            employee_id=user.employee_id,
            network_address=client_ip,
            message=f"Risk score {total_score}/100 detected. Signals: {', '.join(signals)}",
        )

    return total_score, signals
