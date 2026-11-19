"""
Subsystem 1: Tamper-Evident Audit & Cryptographic Hash Chaining Engine.
Implements:
1. Append-only cryptographic hash chaining across AuthEvent records.
2. Signed audit checkpoints (AuditCheckpoint model).
3. Verification engine capable of detecting altered, injected, or deleted audit records.
4. Automatic SecurityAlert generation upon tamper detection.
"""

import hashlib
import hmac
import time
from typing import Optional, Tuple

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import AuditCheckpoint, AuthEvent, SecurityAlert

AUDIT_HMAC_KEY = getattr(settings, "AUDIT_HMAC_KEY", getattr(settings, "SECRET_KEY", "default-audit-key")).encode("utf-8")


def compute_event_hash(prev_hash: str, event: AuthEvent) -> str:
    """
    Computes cryptographic link for an event in the chain:
    H_i = SHA256(H_{i-1} || id || time || event || code || employee_id || message)
    """
    event_str = f"{event.id}|{event.time.isoformat() if event.time else ''}|{event.event}|{event.code}|{event.employee_id}|{event.message}"
    payload = f"{prev_hash}|{event_str}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sign_hash(cumulative_hash: str) -> str:
    """Computes HMAC-SHA256 signature for checkpoint cumulative hash."""
    return hmac.new(AUDIT_HMAC_KEY, cumulative_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def create_audit_checkpoint() -> Optional[AuditCheckpoint]:
    """
    Builds a new signed checkpoint covering all uncheckpointed AuthEvent records.
    Chains forward from the latest checkpoint hash.
    """
    last_checkpoint = AuditCheckpoint.objects.order_by("-created_at").first()

    start_id = last_checkpoint.end_event_id if last_checkpoint else 0
    prev_hash = last_checkpoint.cumulative_hash if last_checkpoint else "GENESIS_SALOW_AUDIT_2026"

    events = AuthEvent.objects.filter(id__gt=start_id).order_by("id")
    if not events.exists():
        return None

    current_hash = prev_hash
    first_id = events.first().id
    last_id = events.last().id
    count = events.count()

    for ev in events:
        current_hash = compute_event_hash(current_hash, ev)

    checkpoint_id = f"CP-{timezone.now().strftime('%Y%m%d%H%M%S')}-{last_id}"
    signature = sign_hash(current_hash)

    checkpoint = AuditCheckpoint.objects.create(
        checkpoint_id=checkpoint_id,
        start_event_id=first_id,
        end_event_id=last_id,
        event_count=count,
        cumulative_hash=current_hash,
        previous_checkpoint_hash=prev_hash,
        signature=signature,
        status="valid",
        verified_at=timezone.now(),
    )

    return checkpoint


def verify_audit_chain() -> Tuple[bool, str]:
    """
    Validates cryptographic integrity of all audit checkpoints and underlying events.
    Returns (True, 'All checkpoints valid') or (False, failure_details).
    Raises CRITICAL SecurityAlert upon detecting tampering.
    """
    checkpoints = AuditCheckpoint.objects.order_by("created_at")
    if not checkpoints.exists():
        return True, "No checkpoints exist to verify."

    expected_prev_hash = "GENESIS_SALOW_AUDIT_2026"

    for cp in checkpoints:
        # 1. Verify previous checkpoint hash linkage
        if cp.previous_checkpoint_hash != expected_prev_hash:
            msg = f"Hash chain break at {cp.checkpoint_id}: expected prev_hash {expected_prev_hash}, got {cp.previous_checkpoint_hash}"
            _record_tamper_alert(cp, msg)
            return False, msg

        # 2. Verify signature
        expected_sig = sign_hash(cp.cumulative_hash)
        if not hmac.compare_digest(cp.signature, expected_sig):
            msg = f"Signature mismatch at {cp.checkpoint_id}: checkpoint signature does not match computed HMAC"
            _record_tamper_alert(cp, msg)
            return False, msg

        # 3. Recompute event hash chain over the underlying events
        events = AuthEvent.objects.filter(
            id__gte=cp.start_event_id,
            id__lte=cp.end_event_id,
        ).order_by("id")

        if events.count() != cp.event_count:
            msg = f"Event count mismatch at {cp.checkpoint_id}: recorded {cp.event_count}, found {events.count()} records"
            _record_tamper_alert(cp, msg)
            return False, msg

        running_hash = cp.previous_checkpoint_hash
        for ev in events:
            running_hash = compute_event_hash(running_hash, ev)

        if running_hash != cp.cumulative_hash:
            msg = f"Cumulative hash mismatch at {cp.checkpoint_id}: records have been altered, deleted, or injected!"
            _record_tamper_alert(cp, msg)
            return False, msg

        # Checkpoint is cryptographically valid
        cp.status = "valid"
        cp.verified_at = timezone.now()
        cp.save(update_fields=["status", "verified_at"])

        expected_prev_hash = cp.cumulative_hash

    return True, "All audit checkpoints verified successfully."


def _record_tamper_alert(checkpoint: AuditCheckpoint, details: str):
    """Triggers CRITICAL SecurityAlert on audit chain tampering."""
    import json
    checkpoint.status = "tampered"
    checkpoint.save(update_fields=["status"])

    SecurityAlert.objects.create(
        category=SecurityAlert.Category.AUDIT_INTEGRITY,
        severity=SecurityAlert.Severity.CRITICAL,
        title=f"AUDIT INTEGRITY VIOLATION: {checkpoint.checkpoint_id}",
        description=json.dumps({
            "checkpoint_id": checkpoint.checkpoint_id,
            "error": details,
            "detected_at": timezone.now().isoformat(),
        }),
        status=SecurityAlert.Status.OPEN,
    )
