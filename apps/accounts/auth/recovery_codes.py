"""
One-Time MFA Recovery Code Management.
Generates 10 single-use recovery codes, stored as keyed HMAC-SHA256 hashes.
"""

import hmac
import secrets

from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import get_activation_hmac_key
from apps.accounts.auth.errors import AuthException
from apps.accounts.models import CustomUser, RecoveryCode


def hash_recovery_code(raw_code: str) -> str:
    """Compute keyed HMAC-SHA256 hash over normalized recovery code."""
    normalized = raw_code.strip().replace("-", "").replace(" ", "").upper()
    key = get_activation_hmac_key()
    return hmac.new(key, normalized.encode("utf-8"), "sha256").hexdigest()


def generate_ten_recovery_codes(user: CustomUser) -> list[str]:
    """
    Generate 10 single-use recovery codes for an employee.
    Invalidates any previous recovery code set.
    """
    raw_codes = []
    objects_to_create = []

    for _ in range(10):
        hex_val = secrets.token_hex(5).upper()
        formatted = f"{hex_val[:5]}-{hex_val[5:]}"
        raw_codes.append(formatted)
        objects_to_create.append(
            RecoveryCode(
                user=user,
                keyed_hash=hash_recovery_code(formatted),
                used_at=None,
            )
        )

    with transaction.atomic():
        RecoveryCode.objects.filter(user=user).delete()
        RecoveryCode.objects.bulk_create(objects_to_create)

        log_auth_event(
            event="recovery_codes_generated",
            code="INFO",
            step="generate_recovery",
            employee_id=user.employee_id,
            message="Generated fresh batch of 10 recovery codes",
        )

    return raw_codes


def redeem_recovery_code(user: CustomUser, raw_code: str) -> bool:
    """
    Validate and redeem a single-use recovery code.
    Marks used_at on success; raises AuthException on invalid or already-used code.
    Guarantees atomic single-use via atomic conditional update.
    """
    input_hash = hash_recovery_code(raw_code)
    now = timezone.now()

    # The conditional update and the success audit record commit together: if the audit write fails
    # (for example "database is locked" under contention), the redemption rolls back and the code
    # stays usable, instead of being consumed while the caller sees an error.
    with transaction.atomic():
        updated = RecoveryCode.objects.filter(
            user=user, keyed_hash=input_hash, used_at__isnull=True
        ).update(used_at=now)
        if updated:
            log_auth_event(
                event="recovery_code_redeemed",
                code="INFO",
                step="redeem_recovery",
                employee_id=user.employee_id,
                message=f"Recovery code successfully redeemed for {user.employee_id}",
            )

    if not updated:
        log_auth_event(
            event="recovery_code_failed",
            code="AUTH-MFA-002",
            step="redeem_recovery",
            employee_id=user.employee_id,
            message="Recovery code was invalid or already consumed",
        )
        raise AuthException(
            "AUTH-MFA-002",
            step="redeem_recovery",
            employee_id=user.employee_id,
            message_override="Recovery code is invalid or has already been used.",
        )

    return True
