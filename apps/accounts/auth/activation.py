"""
One-Time Activation Code Engine.
Generates Crockford Base32 10-char codes with keyed HMAC-SHA256 hashing.
Enforces 5-attempt lockout and single-live-code TiDB constraints.
"""

import hmac
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import (
    ACTIVATION_TTL_HOURS,
    MAX_ACTIVATION_ATTEMPTS,
    get_activation_hmac_key,
)
from apps.accounts.auth.errors import AuthException
from apps.accounts.models import ActivationCode, CustomUser

# Crockford Base32 alphabet: excludes I, L, O, U to prevent visual confusion
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_crockford_code() -> str:
    """Generate 10-character code formatted as XXXXX-XXXXX (~50 bits)."""
    chars = [secrets.choice(CROCKFORD_ALPHABET) for _ in range(10)]
    return f"{''.join(chars[:5])}-{''.join(chars[5:])}"


def hash_code(raw_code: str) -> str:
    """Compute keyed HMAC-SHA256 hash over normalized code."""
    # Normalize: strip spaces, dashes, uppercase
    normalized = raw_code.strip().replace("-", "").replace(" ", "").upper()
    key = get_activation_hmac_key()
    return hmac.new(key, normalized.encode("utf-8"), "sha256").hexdigest()


def issue_activation_code(
    user: CustomUser,
    purpose: str = ActivationCode.Purpose.ACTIVATION,
    issued_by: CustomUser = None,
) -> tuple[ActivationCode, str]:
    """
    Issue a new one-time activation or reset code.
    Invalidates any previous live code for this user and purpose.
    """
    raw_code = generate_crockford_code()
    keyed_hash = hash_code(raw_code)
    expires_at = timezone.now() + timedelta(hours=ACTIVATION_TTL_HOURS)

    with transaction.atomic():
        # De-activate previous live codes by setting is_live=None (TiDB safe)
        ActivationCode.objects.filter(user=user, purpose=purpose, is_live=1).update(is_live=None)

        record = ActivationCode.objects.create(
            user=user,
            purpose=purpose,
            keyed_hash=keyed_hash,
            expires_at=expires_at,
            attempts=0,
            issued_by=issued_by,
            is_live=1,
        )

        log_auth_event(
            event="code_issued",
            code="INFO",
            step="issue_code",
            employee_id=user.employee_id,
            reference_id=f"CODE-{record.id}",
            message=f"Issued {purpose} code expiring at {expires_at.isoformat()}",
        )

    return record, raw_code


def verify_and_consume_code(
    employee_id: str,
    raw_code: str,
    purpose: str = ActivationCode.Purpose.ACTIVATION,
    ip_address: str = None,
) -> tuple[ActivationCode, CustomUser]:
    """
    Validate code and consume it atomically.
    Transitions account from invited to activating on success.
    """
    user = CustomUser.objects.filter(employee_id=employee_id.strip().upper()).first()
    if not user:
        log_auth_event(
            event="activation_failed",
            code="AUTH-ACT-001",
            step="verify_code",
            employee_id=employee_id,
            network_address=ip_address,
            message="Attempt on non-existent employee ID",
        )
        raise AuthException("AUTH-ACT-001", step="verify_code", employee_id=employee_id)

    code_record = ActivationCode.objects.filter(user=user, purpose=purpose, is_live=1).first()

    if not code_record:
        log_auth_event(
            event="activation_failed",
            code="AUTH-ACT-001",
            step="verify_code",
            employee_id=employee_id,
            network_address=ip_address,
            message="No live code found for user and purpose",
        )
        raise AuthException("AUTH-ACT-001", step="verify_code", employee_id=employee_id)

    # Check lock
    if code_record.attempts >= MAX_ACTIVATION_ATTEMPTS:
        log_auth_event(
            event="activation_locked",
            code="AUTH-ACT-002",
            step="verify_code",
            employee_id=employee_id,
            network_address=ip_address,
            message="Code is locked due to 5 failed attempts",
        )
        raise AuthException("AUTH-ACT-002", step="verify_code", employee_id=employee_id)

    # Check expiration
    if timezone.now() > code_record.expires_at:
        code_record.is_live = None
        code_record.save(update_fields=["is_live"])
        log_auth_event(
            event="activation_expired",
            code="AUTH-ACT-001",
            step="verify_code",
            employee_id=employee_id,
            network_address=ip_address,
            message="Code has expired",
        )
        raise AuthException("AUTH-ACT-001", step="verify_code", employee_id=employee_id)

    input_hash = hash_code(raw_code)
    if not hmac.compare_digest(code_record.keyed_hash, input_hash):
        code_record.attempts += 1
        if code_record.attempts >= MAX_ACTIVATION_ATTEMPTS:
            code_record.is_live = None  # Lock it permanently
        code_record.save(update_fields=["attempts", "is_live"])

        log_auth_event(
            event="activation_hash_mismatch",
            code="AUTH-ACT-001",
            step="verify_code",
            employee_id=employee_id,
            network_address=ip_address,
            message=f"Wrong code entry (attempt {code_record.attempts}/{MAX_ACTIVATION_ATTEMPTS})",
        )
        if code_record.attempts >= MAX_ACTIVATION_ATTEMPTS:
            raise AuthException("AUTH-ACT-002", step="verify_code", employee_id=employee_id)
        raise AuthException("AUTH-ACT-001", step="verify_code", employee_id=employee_id)

    # SUCCESS: Atomically consume code
    with transaction.atomic():
        code_record.consumed_at = timezone.now()
        code_record.is_live = None
        code_record.save(update_fields=["consumed_at", "is_live"])

        if user.status == CustomUser.Status.INVITED:
            user.status = CustomUser.Status.ACTIVATING
            user.save(update_fields=["status", "updated_at"])

        log_auth_event(
            event="code_consumed",
            code="INFO",
            step="verify_code",
            employee_id=employee_id,
            network_address=ip_address,
            message=f"{purpose} code successfully validated and consumed",
        )

    return code_record, user
