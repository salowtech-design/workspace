"""Queue, claim and run database backups for the admin Backups page.

Split of authority:
- The web app only queues a BackupRun row and shows status. It never runs a backup inline.
- The worker (manage.py process_backup_jobs) claims a row atomically, runs scripts/tidb_backup_verify.py in a
  child process with a timeout, then records the manifest fields. The child encrypts to the recovery PUBLIC key
  when BACKUP_RECOVERY_PUBLIC_KEY is set, so neither the web app nor the worker holds recovery authority.
- "Scratch restore ok" means the backup-time drill restored the data into scratch tables and compared contents,
  using the per-backup key it just generated. It is not a custodian-authorised restore of the stored file.
- check_archive_integrity only parses the stored manifest and checks size and checksum fields. It does not
  decrypt, and it is not a restore proof.
"""

import json
import os
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import BackupRun

MAX_ATTEMPTS = 3
STALE_RUNNING_AFTER = timedelta(minutes=45)
JOB_TIMEOUT_SECONDS = 40 * 60
MAX_ARCHIVE_BYTES = 2 * 1024**3
SAFE_NAME = re.compile(r"^tidb_full_backup_\d{8}_\d{6}\.enc\.json$")


def storage_dir() -> Path:
    return Path(os.getenv("BACKUP_STORAGE_DIR") or (Path(settings.BASE_DIR) / "backups"))


def queue_backup(*, actor) -> tuple[BackupRun, bool]:
    """Queue one backup. If one is already queued or running, return it instead of piling up another."""
    with transaction.atomic():
        existing = (
            BackupRun.objects.select_for_update()
            .filter(status__in=[BackupRun.Status.QUEUED, BackupRun.Status.RUNNING])
            .first()
        )
        if existing:
            return existing, False
        run = BackupRun.objects.create(
            requested_by_ref=actor.id, requested_by_employee_id=actor.employee_id
        )
        return run, True


def recover_stale_runs(now=None) -> int:
    """Requeue (or fail after MAX_ATTEMPTS) runs whose worker died while running."""
    now = now or timezone.now()
    n = 0
    for run in BackupRun.objects.filter(
        status=BackupRun.Status.RUNNING, started_at__lt=now - STALE_RUNNING_AFTER
    ):
        if run.attempts >= MAX_ATTEMPTS:
            updated = BackupRun.objects.filter(pk=run.pk, status=BackupRun.Status.RUNNING).update(
                status=BackupRun.Status.FAILED,
                finished_at=now,
                error_message="Worker stopped before finishing; retry limit reached.",
            )
        else:
            updated = BackupRun.objects.filter(pk=run.pk, status=BackupRun.Status.RUNNING).update(
                status=BackupRun.Status.QUEUED
            )
        n += updated
    return n


def claim_next_run(now=None) -> BackupRun | None:
    """Atomically move the oldest queued run to running. Only one worker can win a given row."""
    now = now or timezone.now()
    for run_id in (
        BackupRun.objects.filter(status=BackupRun.Status.QUEUED)
        .order_by("id")
        .values_list("id", flat=True)[:5]
    ):
        won = BackupRun.objects.filter(pk=run_id, status=BackupRun.Status.QUEUED).update(
            status=BackupRun.Status.RUNNING, started_at=now, attempts=F("attempts") + 1
        )
        if won:
            return BackupRun.objects.get(pk=run_id)
    return None


def _fail(run: BackupRun, message: str) -> BackupRun:
    BackupRun.objects.filter(pk=run.pk).update(
        status=BackupRun.Status.FAILED,
        finished_at=timezone.now(),
        error_message=message[:500],
    )
    run.refresh_from_db()
    return run


def _newest_archive_since(directory: Path, since_ts: float) -> Path | None:
    candidates = [
        p
        for p in directory.glob("tidb_full_backup_*.enc.json")
        if SAFE_NAME.match(p.name) and p.is_file() and p.stat().st_mtime >= since_ts - 1
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def execute_run(run: BackupRun) -> BackupRun:
    """Run the backup child process for a claimed run and record the outcome. Never raises for job failures."""
    directory = storage_dir()
    started = timezone.now().timestamp()
    env = dict(os.environ, BACKUP_STORAGE_DIR=str(directory))
    script = Path(settings.BASE_DIR) / "scripts" / "tidb_backup_verify.py"
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        proc = subprocess.run(  # noqa: S603
            [sys.executable, str(script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=JOB_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _fail(run, "Backup timed out and was stopped.")
    except OSError as exc:
        return _fail(run, f"Backup could not start: {exc.__class__.__name__}")
    if proc.returncode != 0:
        tail = (proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
        return _fail(run, f"Backup failed (exit {proc.returncode}): {tail[0]}")
    archive = _newest_archive_since(directory, started)
    if archive is None:
        # The script exits 0 when database credentials are not configured. That is not a backup.
        return _fail(run, "No backup file was produced (database credentials not configured?).")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        return _fail(run, "Backup file exceeds the archive size limit.")
    try:
        manifest = json.loads(archive.read_text(encoding="utf-8"))
        os.chmod(archive, 0o600)
    except (OSError, ValueError):
        return _fail(run, "Backup file could not be read back.")
    mode = "envelope-x25519" if manifest.get("format_version") == "3.0-envelope-x25519" else "key"
    BackupRun.objects.filter(pk=run.pk).update(
        status=BackupRun.Status.SUCCEEDED,
        finished_at=timezone.now(),
        file_name=archive.name,
        byte_size=archive.stat().st_size,
        checksum_sha256=str(manifest.get("checksum_sha256", ""))[:64],
        table_count=manifest.get("table_count"),
        total_rows=manifest.get("total_rows"),
        encryption_mode=mode,
        scratch_restore_ok=True,
        error_message="",
    )
    run.refresh_from_db()
    return run


def check_archive_integrity(run: BackupRun) -> tuple[bool, str]:
    """Online integrity check only: file exists inside the storage folder, parses, matches recorded size/checksum.

    Does not decrypt and does not prove the backup can be restored. Restore proof needs the offline console.
    """
    if run.status != BackupRun.Status.SUCCEEDED or not SAFE_NAME.match(run.file_name or ""):
        return False, "No stored archive for this run."
    base = storage_dir().resolve()
    path = (base / run.file_name).resolve()
    if path.parent != base or not path.is_file():
        return False, "Archive file is missing from the storage folder."
    if path.stat().st_size != run.byte_size:
        return False, "Archive file size differs from the recorded size."
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "Archive file is not valid."
    if manifest.get("checksum_sha256") != run.checksum_sha256:
        return False, "Archive checksum field differs from the recorded value."
    if not manifest.get("ciphertext_base64") or not manifest.get("hmac_signature"):
        return False, "Archive is missing encrypted data or its signature."
    return (
        True,
        "Archive file is present and its recorded fields match. This is not a restore test.",
    )
