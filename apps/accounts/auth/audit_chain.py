"""
Subsystem 1: Tamper-Evident Audit & Cryptographic Hash Chaining Engine.
Implements:
1. Append-only cryptographic hash chaining across AuthEvent records.
2. Signed audit checkpoints with independently managed custody keys (fail-closed).
3. Off-database signed checkpoint custody storage for disaster recovery & DB-only attacker defense.
4. Cryptographic verification engine detecting altered, injected, or deleted records.
5. Automatic CRITICAL SecurityAlert generation upon tamper detection.
6. Key rotation with key-ID-bound verification.
"""

import hashlib
import hmac
import json
from pathlib import Path
from typing import Optional, Tuple

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from apps.accounts.models import AuditCheckpoint, AuthEvent, SecurityAlert

GENESIS_HASH = "GENESIS_SALOW_AUDIT_2026"


def get_audit_signing_key(key_id: Optional[str] = None) -> Tuple[str, bytes]:
    """
    Retrieves independently managed audit signing key.
    Fails closed if key is missing, defaults to SECRET_KEY, or uses insecure literals.
    Supports key rotation via key_id and AUDIT_SIGNING_KEYS mapping.
    """
    key_dict = getattr(settings, "AUDIT_SIGNING_KEYS", {})
    active_id = getattr(settings, "AUDIT_ACTIVE_KEY_ID", "k1")
    single_key = getattr(settings, "AUDIT_HMAC_KEY", None)

    target_id = key_id or active_id
    raw_key = None

    if isinstance(key_dict, dict) and target_id in key_dict:
        raw_key = key_dict[target_id]
    elif single_key:
        raw_key = single_key
        target_id = "k1"

    if not raw_key:
        raise ImproperlyConfigured(
            "AUDIT_HMAC_KEY or AUDIT_SIGNING_KEYS must be independently configured. "
            "Audit logging fails closed when signing keys are absent."
        )

    # Insecure fallback and secret collision detection
    django_secret = getattr(settings, "SECRET_KEY", "")
    if raw_key == django_secret:
        raise ImproperlyConfigured(
            "Security Violation: Audit signing key cannot match Django SECRET_KEY. "
            "Independent signing-key custody is mandatory."
        )
    if str(raw_key) in ("default-audit-key", "insecure-default", ""):
        raise ImproperlyConfigured(
            "Insecure audit signing key detected. A cryptographically sound key is required."
        )

    if isinstance(raw_key, str):
        raw_key = raw_key.encode("utf-8")

    return target_id, raw_key


def get_checkpoint_storage_dir() -> Path:
    """Returns directory path for independent off-database checkpoint custody."""
    checkpoint_dir = getattr(
        settings,
        "AUDIT_CHECKPOINT_DIR",
        settings.BASE_DIR / "var" / "audit_checkpoints",
    )
    p = Path(checkpoint_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def compute_event_hash(prev_hash: str, event: AuthEvent) -> str:
    """
    Computes cryptographic link for an event in the chain:
    H_i = SHA256(H_{i-1} || id || time || event || code || employee_id || message)
    """
    event_str = f"{event.id}|{event.time.isoformat() if event.time else ''}|{event.event}|{event.code}|{event.employee_id}|{event.message}"
    payload = f"{prev_hash}|{event_str}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sign_hash(cumulative_hash: str, key_id: Optional[str] = None) -> Tuple[str, str]:
    """Computes HMAC-SHA256 signature for checkpoint cumulative hash using independent key."""
    used_key_id, key_bytes = get_audit_signing_key(key_id=key_id)
    sig = hmac.new(key_bytes, cumulative_hash.encode("utf-8"), hashlib.sha256).hexdigest()
    return used_key_id, sig


def export_checkpoint_off_db(checkpoint: AuditCheckpoint) -> Path:
    """
    Exports a signed audit checkpoint to independent off-database storage.
    Includes a file-level tamper seal to ensure integrity of the off-database backup.
    """
    storage_dir = get_checkpoint_storage_dir()
    file_path = storage_dir / f"{checkpoint.checkpoint_id}.json"

    data = {
        "checkpoint_id": checkpoint.checkpoint_id,
        "start_event_id": checkpoint.start_event_id,
        "end_event_id": checkpoint.end_event_id,
        "event_count": checkpoint.event_count,
        "cumulative_hash": checkpoint.cumulative_hash,
        "previous_checkpoint_hash": checkpoint.previous_checkpoint_hash,
        "key_id": checkpoint.key_id,
        "signature": checkpoint.signature,
        "created_at": checkpoint.created_at.isoformat() if checkpoint.created_at else timezone.now().isoformat(),
        "status": checkpoint.status,
    }

    # Generate custody seal over serialized content
    canonical_payload = json.dumps(data, sort_keys=True)
    _, custody_seal = sign_hash(canonical_payload, key_id=checkpoint.key_id)

    full_record = {
        "record": data,
        "custody_seal": custody_seal,
        "exported_at": timezone.now().isoformat(),
    }

    file_path.write_text(json.dumps(full_record, indent=2), encoding="utf-8")
    return file_path


def create_audit_checkpoint() -> Optional[AuditCheckpoint]:
    """
    Builds a new signed checkpoint covering all uncheckpointed AuthEvent records.
    Chains forward from the latest checkpoint hash, writes to primary database,
    and synchronizes an independent signed checkpoint into off-database storage.
    """
    last_checkpoint = AuditCheckpoint.objects.order_by("-created_at").first()

    start_id = last_checkpoint.end_event_id if last_checkpoint else 0
    prev_hash = last_checkpoint.cumulative_hash if last_checkpoint else GENESIS_HASH

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
    key_id, signature = sign_hash(current_hash)

    checkpoint = AuditCheckpoint.objects.create(
        checkpoint_id=checkpoint_id,
        start_event_id=first_id,
        end_event_id=last_id,
        event_count=count,
        cumulative_hash=current_hash,
        previous_checkpoint_hash=prev_hash,
        key_id=key_id,
        signature=signature,
        status="valid",
        verified_at=timezone.now(),
    )

    # Store in independent off-database storage
    try:
        export_checkpoint_off_db(checkpoint)
    except Exception as exc:
        _record_tamper_alert(
            checkpoint,
            f"Failed to persist checkpoint to independent custody storage: {exc}",
        )

    return checkpoint


def verify_audit_chain() -> Tuple[bool, str]:
    """
    Validates cryptographic integrity of all audit checkpoints and underlying events,
    cross-verifying primary database state against independent off-database custody records.
    Detects:
    1. Primary DB record alteration/deletion/injection.
    2. Primary DB checkpoint deletion or tampering (database-only attacker scenario).
    3. Off-DB custody file alteration or desynchronization.
    4. Signature mismatch or unauthorized key rotation.
    Raises CRITICAL SecurityAlert upon detecting tampering.
    """
    checkpoints = list(AuditCheckpoint.objects.order_by("created_at"))
    storage_dir = get_checkpoint_storage_dir()

    if not checkpoints:
        # Check if off-database checkpoints exist (detects complete DB wipe/truncation attack)
        off_db_files = list(storage_dir.glob("CP-*.json"))
        if off_db_files:
            msg = f"DATABASE ATTACK DETECTED: Primary database has 0 checkpoints, but {len(off_db_files)} independent custody checkpoints exist!"
            _record_orphan_tamper_alert("ALL_CHECKPOINTS_WIPED", msg)
            return False, msg
        return True, "No checkpoints exist to verify."

    expected_prev_hash = GENESIS_HASH

    for cp in checkpoints:
        # 1. Verify previous checkpoint hash linkage
        if cp.previous_checkpoint_hash != expected_prev_hash:
            msg = f"Hash chain break at {cp.checkpoint_id}: expected prev_hash {expected_prev_hash}, got {cp.previous_checkpoint_hash}"
            _record_tamper_alert(cp, msg)
            return False, msg

        # 2. Verify signature with key_id
        try:
            _, expected_sig = sign_hash(cp.cumulative_hash, key_id=cp.key_id)
            if not hmac.compare_digest(cp.signature, expected_sig):
                msg = f"Signature mismatch at {cp.checkpoint_id}: checkpoint signature does not match computed HMAC with key {cp.key_id}"
                _record_tamper_alert(cp, msg)
                return False, msg
        except ImproperlyConfigured as e:
            msg = f"Key custody failure at {cp.checkpoint_id}: {e}"
            _record_tamper_alert(cp, msg)
            return False, msg

        # 3. Cross-verify against independent off-database custody storage
        off_db_file = storage_dir / f"{cp.checkpoint_id}.json"
        if off_db_file.exists():
            try:
                raw_custody = json.loads(off_db_file.read_text(encoding="utf-8"))
                rec = raw_custody.get("record", {})
                file_seal = raw_custody.get("custody_seal", "")

                # Verify custody file seal
                canonical_payload = json.dumps(rec, sort_keys=True)
                _, expected_seal = sign_hash(canonical_payload, key_id=rec.get("key_id", cp.key_id))
                if not hmac.compare_digest(file_seal, expected_seal):
                    msg = f"Custody seal mismatch for off-DB checkpoint file {off_db_file.name}."
                    _record_tamper_alert(cp, msg)
                    return False, msg

                if rec.get("cumulative_hash") != cp.cumulative_hash:
                    msg = f"Discrepancy between DB and independent storage for {cp.checkpoint_id}: DB has {cp.cumulative_hash}, custody file has {rec.get('cumulative_hash')}"
                    _record_tamper_alert(cp, msg)
                    return False, msg
            except Exception as e:
                msg = f"Failed reading independent custody record for {cp.checkpoint_id}: {e}"
                _record_tamper_alert(cp, msg)
                return False, msg
        else:
            # Sync or warn if custody file is missing
            try:
                export_checkpoint_off_db(cp)
            except Exception as e:
                msg = f"Missing independent custody file for {cp.checkpoint_id} and export failed: {e}"
                _record_tamper_alert(cp, msg)
                return False, msg

        # 4. Recompute event hash chain over underlying events
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

    return True, "All audit checkpoints verified successfully against database and independent custody storage."


def _record_tamper_alert(checkpoint: AuditCheckpoint, details: str):
    """Triggers CRITICAL SecurityAlert on audit chain tampering."""
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


def _record_orphan_tamper_alert(checkpoint_id: str, details: str):
    """Triggers CRITICAL SecurityAlert when DB checkpoints have been wiped or orphaned."""
    SecurityAlert.objects.create(
        category=SecurityAlert.Category.AUDIT_INTEGRITY,
        severity=SecurityAlert.Severity.CRITICAL,
        title=f"AUDIT INTEGRITY VIOLATION: {checkpoint_id}",
        description=json.dumps({
            "checkpoint_id": checkpoint_id,
            "error": details,
            "detected_at": timezone.now().isoformat(),
        }),
        status=SecurityAlert.Status.OPEN,
    )
