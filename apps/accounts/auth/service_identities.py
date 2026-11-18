"""
Service Identities & Machine-to-Machine (M2M) API Security Engine.
Provides cryptographically hashed API token issuance, scope enforcement,
and lifecycle management for daemon, worker, and integration services.
"""

import hmac
import secrets
from datetime import datetime
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import get_activation_hmac_key
from apps.accounts.models import CustomUser, ServiceIdentity

VALID_SCOPES = {
    "attendance:read": "Read attendance logs and session reports",
    "attendance:write": "Create and update verified attendance records",
    "directory:read": "Read company directory, teams, and departments",
    "certificates:verify": "Verify cryptographic certificate signatures",
    "certificates:issue": "Issue verified digital certificates",
    "audit:write": "Append immutable security and operational audit records",
    "outbox:deliver": "Process and deliver queued transactional notifications",
    "iam:monitor": "Read non-sensitive IAM telemetry and live stream",
}


def hash_service_secret(raw_secret: str) -> str:
    """Compute keyed HMAC-SHA256 over raw service token."""
    key = get_activation_hmac_key()
    return hmac.new(key, (raw_secret or "").strip().encode("utf-8"), "sha256").hexdigest()


def issue_service_identity(
    name: str,
    client_id: str,
    scopes: list[str],
    created_by: Optional[CustomUser] = None,
    expires_at: Optional[datetime] = None,
) -> tuple[ServiceIdentity, str]:
    """
    Issue a new Service Identity with a cryptographically secure token.
    Returns (ServiceIdentity record, raw_unhashed_token).
    The raw token is displayed exactly once and never stored unhashed.
    """
    clean_name = (name or "").strip()
    clean_cid = (client_id or "").strip().lower()

    if not clean_name:
        raise ValidationError("Service name is required.")
    if not clean_cid:
        raise ValidationError("Service client ID is required.")
    if not scopes:
        raise ValidationError("At least one valid scope must be assigned.")
    if ServiceIdentity.objects.filter(client_id=clean_cid).exists():
        raise ValidationError(f"Client ID '{clean_cid}' is already in use.")

    # Validate scopes
    invalid_scopes = [s for s in scopes if s not in VALID_SCOPES]
    if invalid_scopes:
        raise ValidationError(f"Unrecognized scopes requested: {', '.join(invalid_scopes)}")

    # Validate expiration
    if expires_at and expires_at <= timezone.now():
        raise ValidationError("Expiration timestamp must be in the future.")

    # Generate raw token with standard prefix
    raw_token = f"salow_svc_{secrets.token_urlsafe(32)}"
    secret_hash = hash_service_secret(raw_token)

    identity = ServiceIdentity.objects.create(
        name=clean_name,
        client_id=clean_cid,
        secret_hash=secret_hash,
        scopes=scopes,
        is_active=True,
        is_non_interactive=True,
        expires_at=expires_at,
        created_by=created_by,
    )

    actor_str = created_by.employee_id if created_by else "system"
    log_auth_event(
        event="service_identity_issued",
        code="INFO",
        step="service_auth",
        employee_id=actor_str,
        message=f"Issued ServiceIdentity '{identity.client_id}' with scopes: {','.join(scopes)}",
    )

    return identity, raw_token


def verify_service_identity(
    client_id: str,
    raw_secret: str,
    required_scope: Optional[str] = None,
    request_ip: Optional[str] = None,
) -> ServiceIdentity:
    """
    Verify client credentials and scope grant with timing-safe protection.
    Updates last_used_at timestamp on success.
    """
    clean_cid = (client_id or "").strip().lower()
    clean_secret = (raw_secret or "").strip()

    if not clean_cid or not clean_secret:
        log_auth_event(
            event="service_auth_failed_missing_credentials",
            code="AUTH-SVC-001",
            step="service_auth",
            network_address=request_ip,
            message="Missing client ID or secret in service verification",
        )
        raise PermissionDenied("Invalid service credentials.")

    identity = ServiceIdentity.objects.filter(client_id=clean_cid).first()
    if not identity:
        # Timing-safe dummy hash comparison to resist client ID enumeration probes
        dummy_hash = hash_service_secret("salow_svc_dummy_timing_protection_pad")
        hmac.compare_digest(dummy_hash, hash_service_secret(clean_secret))
        log_auth_event(
            event="service_auth_failed_not_found",
            code="AUTH-SVC-001",
            step="service_auth",
            network_address=request_ip,
            message=f"Unknown client ID '{clean_cid}'",
        )
        raise PermissionDenied("Invalid service credentials.")

    if not identity.is_active:
        log_auth_event(
            event="service_auth_failed_inactive",
            code="AUTH-SVC-002",
            step="service_auth",
            network_address=request_ip,
            message=f"Service '{clean_cid}' is deactivated or revoked",
        )
        raise PermissionDenied("Service identity is revoked or inactive.")

    if identity.expires_at and identity.expires_at < timezone.now():
        log_auth_event(
            event="service_auth_failed_expired",
            code="AUTH-SVC-003",
            step="service_auth",
            network_address=request_ip,
            message=f"Service '{clean_cid}' credential expired",
        )
        raise PermissionDenied("Service identity credentials have expired.")

    expected_hash = identity.secret_hash
    provided_hash = hash_service_secret(clean_secret)
    if not hmac.compare_digest(expected_hash, provided_hash):
        log_auth_event(
            event="service_auth_failed_invalid_secret",
            code="AUTH-SVC-001",
            step="service_auth",
            network_address=request_ip,
            message=f"Invalid secret provided for '{clean_cid}'",
        )
        raise PermissionDenied("Invalid service credentials.")

    if required_scope and required_scope not in identity.scopes:
        log_auth_event(
            event="service_auth_failed_missing_scope",
            code="AUTH-SVC-004",
            step="service_auth",
            network_address=request_ip,
            message=f"Service '{clean_cid}' lacks required scope '{required_scope}'",
        )
        raise PermissionDenied(f"Service lacks required scope: {required_scope}")

    now = timezone.now()
    ServiceIdentity.objects.filter(id=identity.id).update(last_used_at=now)
    identity.last_used_at = now

    log_auth_event(
        event="service_auth_success",
        code="INFO",
        step="service_auth",
        network_address=request_ip,
        message=f"Service '{clean_cid}' successfully authenticated",
    )
    return identity


def revoke_service_identity(client_id: str, actor: Optional[CustomUser] = None) -> bool:
    """Revoke a service identity."""
    clean_cid = (client_id or "").strip().lower()
    identity = ServiceIdentity.objects.filter(client_id=clean_cid).first()
    if not identity:
        return False

    identity.is_active = False
    identity.save(update_fields=["is_active"])

    actor_str = actor.employee_id if actor else "system"
    log_auth_event(
        event="service_identity_revoked",
        code="WARN",
        step="service_auth",
        employee_id=actor_str,
        message=f"Revoked ServiceIdentity '{clean_cid}'",
    )
    return True
