"""
One-Time Activation Code Engine.
Generates Crockford Base32 10-char codes with keyed HMAC-SHA256 hashing.
Enforces 5-attempt lockout and single-live-code TiDB constraints.
"""

import hmac
import secrets
from datetime import timedelta
from typing import Optional

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
    raw = str(raw_code or "")
    normalized = raw.strip().replace("-", "").replace(" ", "").upper()
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


def _check_code_preconditions(
    code_record: ActivationCode | None,
    employee_id: str,
    ip_address: str | None,
) -> AuthException | None:
    """Validate existence, lock state, and expiration of activation code."""
    if not code_record:
        log_auth_event(
            event="activation_failed",
            code="AUTH-ACT-001",
            step="verify_code",
            employee_id=employee_id,
            network_address=ip_address,
            message="No live code found for user and purpose",
        )
        return AuthException("AUTH-ACT-001", step="verify_code", employee_id=employee_id)

    if code_record.attempts >= MAX_ACTIVATION_ATTEMPTS:
        log_auth_event(
            event="activation_locked",
            code="AUTH-ACT-002",
            step="verify_code",
            employee_id=employee_id,
            network_address=ip_address,
            message="Code is locked due to 5 failed attempts",
        )
        return AuthException("AUTH-ACT-002", step="verify_code", employee_id=employee_id)

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
        return AuthException("AUTH-ACT-001", step="verify_code", employee_id=employee_id)

    return None


def verify_and_consume_code(
    employee_id: str,
    raw_code: str,
    purpose: str = ActivationCode.Purpose.ACTIVATION,
    ip_address: str = None,
) -> tuple[ActivationCode, CustomUser]:
    """
    Validate code and consume it atomically.
    Transitions account from invited to activating on success.
    Enforces atomic attempts and consumption under select_for_update().
    """
    if not employee_id or not isinstance(employee_id, str):
        log_auth_event(
            event="activation_failed",
            code="AUTH-ACT-001",
            step="verify_code",
            employee_id="UNKNOWN",
            network_address=ip_address,
            message="Attempt with missing employee ID",
        )
        raise AuthException("AUTH-ACT-001", step="verify_code", employee_id=str(employee_id))

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

    auth_error_to_raise = None
    with transaction.atomic():
        code_record = (
            ActivationCode.objects.select_for_update()
            .filter(user=user, purpose=purpose, is_live=1)
            .first()
        )

        auth_error_to_raise = _check_code_preconditions(code_record, employee_id, ip_address)

        if not auth_error_to_raise:
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
                    auth_error_to_raise = AuthException(
                        "AUTH-ACT-002", step="verify_code", employee_id=employee_id
                    )
                else:
                    auth_error_to_raise = AuthException(
                        "AUTH-ACT-001", step="verify_code", employee_id=employee_id
                    )
            else:
                # SUCCESS: Atomically consume code
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

    if auth_error_to_raise:
        raise auth_error_to_raise

    return code_record, user


def issue_email_token(
    user: CustomUser,
    purpose: str,
    ttl_minutes: int = 15,
    ttl_seconds: Optional[int] = None,
) -> tuple[ActivationCode, str]:
    """
    Issue a high-entropy, cryptographically secure URL-safe token.
    Stores keyed HMAC-SHA256 verifier with short expiration.
    Deactivates any previous live token for this user and purpose.
    Supports either 30-second strict window or minute-based TTL.
    """
    raw_token = secrets.token_urlsafe(32)
    keyed_hash = hash_code(raw_token)
    if ttl_seconds is not None:
        expires_at = timezone.now() + timedelta(seconds=ttl_seconds)
        expiry_desc = f"{ttl_seconds}s"
    else:
        expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
        expiry_desc = f"{ttl_minutes}m"

    with transaction.atomic():
        ActivationCode.objects.filter(user=user, purpose=purpose, is_live=1).update(is_live=None)
        record = ActivationCode.objects.create(
            user=user,
            purpose=purpose,
            keyed_hash=keyed_hash,
            expires_at=expires_at,
            attempts=0,
            is_live=1,
        )

        log_auth_event(
            event="email_token_issued",
            code="INFO",
            step="issue_email_token",
            employee_id=user.employee_id,
            reference_id=f"TOKEN-{record.id}",
            message=f"Issued email token [{purpose}] expiring in {expiry_desc}",
        )

    return record, raw_token


def verify_email_token_safe(
    raw_token: str,
    purpose: str,
) -> tuple[ActivationCode, CustomUser]:
    """
    Validate email token without consuming it (read-only for GET requests).
    Raises AuthException on invalid, expired, or locked token.
    """
    if not raw_token or not isinstance(raw_token, str):
        raise AuthException("AUTH-ACT-001", step="verify_email_token_safe", employee_id="UNKNOWN")

    input_hash = hash_code(raw_token)
    code_record = (
        ActivationCode.objects.filter(keyed_hash=input_hash, purpose=purpose, is_live=1)
        .select_related("user")
        .first()
    )

    if not code_record:
        raise AuthException(
            "AUTH-ACT-001",
            step="verify_email_token_safe",
            employee_id="UNKNOWN",
            message_override="The recovery or verification link is invalid or has already been used.",
        )

    if timezone.now() > code_record.expires_at:
        code_record.is_live = None
        code_record.save(update_fields=["is_live"])
        raise AuthException(
            "AUTH-ACT-001",
            step="verify_email_token_safe",
            employee_id=code_record.user.employee_id,
            message_override="The recovery or verification link has expired.",
        )

    return code_record, code_record.user


def consume_email_token(
    raw_token: str,
    purpose: str,
) -> tuple[ActivationCode, CustomUser]:
    """
    Atomically validate and consume an email token (single-use under select_for_update).
    """
    if not raw_token or not isinstance(raw_token, str):
        raise AuthException("AUTH-ACT-001", step="consume_email_token", employee_id="UNKNOWN")

    input_hash = hash_code(raw_token)
    with transaction.atomic():
        code_record = (
            ActivationCode.objects.select_for_update()
            .filter(keyed_hash=input_hash, purpose=purpose, is_live=1)
            .select_related("user")
            .first()
        )

        if not code_record:
            raise AuthException(
                "AUTH-ACT-001",
                step="consume_email_token",
                employee_id="UNKNOWN",
                message_override="The recovery or verification link is invalid or has already been used.",
            )

        if timezone.now() > code_record.expires_at:
            code_record.is_live = None
            code_record.save(update_fields=["is_live"])
            raise AuthException(
                "AUTH-ACT-001",
                step="consume_email_token",
                employee_id=code_record.user.employee_id,
                message_override="The recovery or verification link has expired.",
            )

        # Burn token atomically
        code_record.consumed_at = timezone.now()
        code_record.is_live = None
        code_record.save(update_fields=["consumed_at", "is_live"])

        log_auth_event(
            event="email_token_consumed",
            code="INFO",
            step="consume_email_token",
            employee_id=code_record.user.employee_id,
            reference_id=f"TOKEN-{code_record.id}",
            message=f"Email token [{purpose}] successfully consumed",
        )

    return code_record, code_record.user
