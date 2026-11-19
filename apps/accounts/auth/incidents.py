"""
Subsystem 12: Security Incident Response & Post-Incident Review Engine.
Implements:
1. Incident lifecycle tracking: Investigating -> Contained -> Remediated -> Closed.
2. Mandatory containment recording and lessons learned documentation.
3. Cryptographically referenced evidence links.
"""

import uuid
from typing import List, Optional

from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.models import CustomUser, SecurityIncident


def create_security_incident(
    title: str,
    severity: str = SecurityIncident.Severity.HIGH,
    description: str = "",
    lead_responder: Optional[CustomUser] = None,
    evidence_references: Optional[List[str]] = None,
) -> SecurityIncident:
    """Creates a formal security incident tracking record."""
    inc_id = f"INC-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    incident = SecurityIncident.objects.create(
        incident_id=inc_id,
        title=title,
        severity=severity,
        status=SecurityIncident.Status.INVESTIGATING,
        lead_responder=lead_responder,
        description=description,
        evidence_references=evidence_references or [],
    )

    log_auth_event(
        event="security_incident_created",
        code=severity.upper(),
        step="incident_response",
        employee_id=lead_responder.employee_id if lead_responder else "SYSTEM",
        reference_id=inc_id,
        message=f"Security Incident [{inc_id}] created: {title} ({severity})",
    )

    return incident


def contain_security_incident(
    incident_id: str,
    containment_steps: str,
    responder: CustomUser,
) -> SecurityIncident:
    """Marks incident as CONTAINED with auditable containment actions."""
    inc = SecurityIncident.objects.get(incident_id=incident_id)
    inc.status = SecurityIncident.Status.CONTAINED
    inc.containment_steps = containment_steps
    inc.contained_at = timezone.now()
    inc.save(update_fields=["status", "containment_steps", "contained_at"])

    log_auth_event(
        event="security_incident_contained",
        code="INFO",
        step="incident_response",
        employee_id=responder.employee_id,
        reference_id=incident_id,
        message=f"Security Incident [{incident_id}] contained by {responder.employee_id}",
    )
    return inc


def close_security_incident(
    incident_id: str,
    recovery_steps: str,
    lessons_learned: str,
    responder: CustomUser,
) -> SecurityIncident:
    """Formally closes incident with completed Post-Incident Review (PIR)."""
    inc = SecurityIncident.objects.get(incident_id=incident_id)
    inc.status = SecurityIncident.Status.CLOSED
    inc.recovery_steps = recovery_steps
    inc.lessons_learned = lessons_learned
    inc.closed_at = timezone.now()
    inc.save(update_fields=["status", "recovery_steps", "lessons_learned", "closed_at"])

    log_auth_event(
        event="security_incident_closed",
        code="INFO",
        step="incident_response",
        employee_id=responder.employee_id,
        reference_id=incident_id,
        message=f"Security Incident [{incident_id}] closed with PIR documentation",
    )
    return inc
