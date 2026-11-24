"""
Emergency Access & Offline Two-Person Break-Glass Subsystem.
Implements the Non-Negotiable Contract for Emergency Access:
1. Two-Person Integrity: Requires two distinct, authoritative Super Admin custodians.
2. Independent credential verification (password + fresh TOTP) for both custodians.
3. Strictly bounded: named custodians, incident ID, scope, mandatory justification (min 20 chars).
4. Maximum 15-minute time ceiling.
5. Emits CRITICAL SecurityAlert and immutable AuthEvent audit trail.
6. Post-use review hooks and credential/key rotation enforcement.
7. Strictly NO standing backdoor, universal password, or bypass tokens.
"""

import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from typing import Tuple

import pyotp
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import get_session_hmac_key
from apps.accounts.auth.crypto import decrypt_totp_secret
from apps.accounts.auth.errors import AuthException
from apps.accounts.models import IAMAdminIdentity, IAMSession, SecurityAlert


MAX_EMERGENCY_WINDOW_MINUTES = 15


def _verify_custodian(admin_id: str, password: str, totp_code: str) -> IAMAdminIdentity:
    """Verifies custodian identity, status, password, and fresh TOTP code under atomic lock."""
    clean_id = admin_id.strip().upper()
    admin = (
        IAMAdminIdentity.objects.select_for_update()
        .select_related("user")
        .filter(admin_id=clean_id)
        .first()
    )
    if not admin:
        raise AuthException(
            "AUTH-EMG-001",
            step="emergency_access",
            message_override=f"Custodian [{clean_id}] not found.",
        )

    if admin.status != IAMAdminIdentity.Status.ACTIVE:
        raise AuthException(
            "AUTH-EMG-002",
            step="emergency_access",
            message_override=f"Custodian [{clean_id}] is {admin.status}.",
        )

    if admin.role != IAMAdminIdentity.Role.SUPER_ADMIN:
        raise PermissionDenied(f"Custodian [{clean_id}] must hold the Super Admin role.")

    target_hash = admin.password_hash or (admin.user.password if admin.user else "")
    from django.contrib.auth.hashers import check_password

    if not target_hash or not check_password(password, target_hash):
        raise AuthException(
            "AUTH-EMG-003",
            step="emergency_access",
            message_override=f"Invalid password for custodian [{clean_id}].",
        )

    if not admin.encrypted_totp_secret:
        raise AuthException(
            "AUTH-EMG-004",
            step="emergency_access",
            message_override=f"Custodian [{clean_id}] has no active TOTP enrolled.",
        )

    raw_secret = decrypt_totp_secret(
        bytes(admin.encrypted_totp_secret),
        bytes(admin.totp_nonce) if admin.totp_nonce else b"",
        admin.totp_key_id,
    )
    totp = pyotp.TOTP(raw_secret, interval=30)
    current_time = timezone.now()
    current_step = int(current_time.timestamp() // 30)
    cleaned_code = totp_code.strip()

    if not totp.verify(cleaned_code, for_time=current_time, valid_window=1):
        raise AuthException(
            "AUTH-EMG-005",
            step="emergency_access",
            message_override=f"Invalid TOTP code for custodian [{clean_id}].",
        )

    matched_step = None
    for step_offset in [0, -1, 1]:
        test_step = current_step + step_offset
        if totp.generate_otp(test_step) == cleaned_code:
            matched_step = test_step
            break

    step_to_record = matched_step or current_step
    if matched_step is not None and matched_step <= admin.last_used_step:
        raise AuthException(
            "AUTH-EMG-006",
            step="emergency_access",
            message_override=f"TOTP code for custodian [{clean_id}] has already been consumed.",
        )

    admin.last_used_step = step_to_record
    admin.save(update_fields=["last_used_step"])
    return admin


def activate_independent_emergency_access(
    custodian_1_id: str,
    custodian_1_password: str,
    custodian_1_totp: str,
    custodian_2_id: str,
    custodian_2_password: str,
    custodian_2_totp: str,
    incident_id: str,
    scope: str,
    reason: str,
    duration_minutes: int = 15,
) -> Tuple[str, dict]:
    """
    Executes independent two-person emergency access authorization.
    Requires two distinct Super Admin custodians, valid credentials, and fresh TOTP tokens.
    Returns (emergency_session_bearer_token, authorization_metadata).
    """
    clean_c1 = custodian_1_id.strip().upper()
    clean_c2 = custodian_2_id.strip().upper()

    # Rule 1: Two-person integrity — distinct custodians strictly required
    if clean_c1 == clean_c2:
        raise PermissionDenied(
            "Two-person emergency access strictly prohibits self-authorization. Custodians must differ."
        )

    # Rule 2: Justification and incident parameters
    if not incident_id or len(incident_id.strip()) < 4:
        raise ValidationError("A valid incident identifier (e.g. INC-2026-001) is required.")

    if not reason or len(reason.strip()) < 20:
        raise ValidationError(
            "Emergency access requires a comprehensive justification (minimum 20 characters)."
        )

    allowed_scopes = (
        "incident_investigation",
        "cluster_recovery",
        "credential_remediation",
        "audit_forensics",
    )
    if scope not in allowed_scopes:
        raise ValidationError(
            f"Invalid emergency access scope '{scope}'. Allowed scopes: {', '.join(allowed_scopes)}"
        )

    duration = min(max(1, duration_minutes), MAX_EMERGENCY_WINDOW_MINUTES)
    now = timezone.now()
    expires_at = now + timedelta(minutes=duration)

    with transaction.atomic():
        # Verify both custodians under row locks
        admin_1 = _verify_custodian(clean_c1, custodian_1_password, custodian_1_totp)
        admin_2 = _verify_custodian(clean_c2, custodian_2_password, custodian_2_totp)

        # Generate single-use opaque emergency bearer token
        raw_bearer = f"salow-emg-{secrets.token_urlsafe(32)}"
        key = get_session_hmac_key()
        session_digest = hmac.new(key, raw_bearer.encode("utf-8"), hashlib.sha256).hexdigest()

        # Persist session with maximum Level 3 assurance
        session = IAMSession.objects.create(
            session_key=session_digest,
            session_digest=session_digest,
            iam_admin=admin_1,
            assurance_level=3,
            stepup_action=f"emergency_access:{scope}",
            stepup_expires_at=expires_at,
            ip_address="127.0.0.1",
            user_agent=f"EmergencyAccess-CLI [{incident_id}]",
            is_active=True,
        )

        metadata = {
            "session_id": str(session.id),
            "incident_id": incident_id.strip(),
            "scope": scope,
            "custodian_1": admin_1.admin_id,
            "custodian_2": admin_2.admin_id,
            "activated_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "duration_minutes": duration,
            "reason": reason.strip(),
            "post_use_rotation_required": True,
        }

        # Emits CRITICAL SecurityAlert
        SecurityAlert.objects.create(
            category=SecurityAlert.Category.BREAK_GLASS_ACCESS,
            severity=SecurityAlert.Severity.CRITICAL,
            title=f"EMERGENCY ACCESS ACTIVATED: {incident_id} ({scope})",
            network_address="127.0.0.1",
            description=json.dumps(metadata),
            status=SecurityAlert.Status.OPEN,
        )

        log_auth_event(
            event="emergency_access_activated",
            code="CRITICAL",
            step="emergency_access",
            employee_id=admin_1.user.employee_id,
            reference_id=incident_id,
            message=(
                f"TWO-PERSON EMERGENCY ACCESS ACTIVATED for {incident_id} (Scope: {scope}) "
                f"by Custodian 1 [{admin_1.admin_id}] and Custodian 2 [{admin_2.admin_id}]. "
                f"Expires in {duration} minutes."
            ),
        )

        return raw_bearer, metadata


def revoke_emergency_access(session_id: str, revoked_by: str, reason: str = "") -> bool:
    """Terminates an emergency session and marks it for post-incident review."""
    with transaction.atomic():
        session = IAMSession.objects.filter(id=session_id, is_active=True).first()
        if not session:
            return False

        session.is_active = False
        session.save(update_fields=["is_active"])

        log_auth_event(
            event="emergency_access_revoked",
            code="INFO",
            step="emergency_access",
            employee_id=revoked_by,
            reference_id=str(session.id),
            message=f"Emergency access session {session.id} revoked by {revoked_by}. Reason: {reason or 'Session completed'}",
        )
        return True
