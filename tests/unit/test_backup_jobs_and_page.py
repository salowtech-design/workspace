"""Backups page and worker: permissions, queueing, atomic claiming, retries, honest status, safe file access."""

import json
import subprocess
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.auth import backup_jobs
from apps.accounts.auth.stepup import grant_sudo
from apps.accounts.models import AuthEvent, BackupRun, CustomUser


def _user(emp, role):
    u = CustomUser.objects.create(
        employee_id=emp,
        full_name=emp,
        email=f"{emp.lower()}@salowtech.com",
        email_verified=True,
        role=role,
        status=CustomUser.Status.ACTIVE,
    )
    u.set_password("AdminSecurePassword123!@#")
    u.save()
    return u


@pytest.fixture
def sa(db):
    import pyotp

    from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
    from apps.accounts.models import TOTPCredential

    u = _user("ST-BK-0001", CustomUser.Role.SUPER_ADMIN)
    ct, nonce, key_id = encrypt_secret(pyotp.random_base32(length=32), get_auth_key_ring())
    TOTPCredential.objects.create(
        user=u, encrypted_secret=ct, nonce=nonce, key_id=key_id, confirmed_at=timezone.now()
    )
    return u


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_STORAGE_DIR", str(tmp_path))
    return tmp_path


def test_only_permitted_role_can_open_page(client, db):
    client.force_login(_user("ST-BK-0002", CustomUser.Role.EMPLOYEE))
    assert client.get(reverse("accounts:admin_backups")).status_code == 403
    assert client.post(reverse("accounts:admin_backup_run")).status_code == 403
    assert BackupRun.objects.count() == 0


def test_sudo_required_before_page_or_queue(client, sa):
    client.force_login(sa)
    r = client.get(reverse("accounts:admin_backups"))
    assert r.status_code == 302 and "sudo" in r["Location"]
    r = client.post(reverse("accounts:admin_backup_run"))
    assert r.status_code == 302 and "sudo" in r["Location"]
    assert BackupRun.objects.count() == 0


def test_page_is_honest_and_has_no_restore_action(client, sa, storage):
    client.force_login(sa)
    grant_sudo(sa)
    html = client.get(reverse("accounts:admin_backups")).content.decode()
    assert "Run backup" in html
    assert "not an off-site copy" in html
    assert "does not decrypt or restore anything" in html
    assert 'style="' not in html
    assert "/restore" not in html
    assert "Restore</button>" not in html


def test_run_button_queues_once_and_audits(client, sa, storage):
    client.force_login(sa)
    grant_sudo(sa)
    assert client.get(reverse("accounts:admin_backup_run")).status_code == 405
    client.post(reverse("accounts:admin_backup_run"))
    client.post(reverse("accounts:admin_backup_run"))
    assert BackupRun.objects.filter(status="queued").count() == 1
    assert AuthEvent.objects.filter(event="backup_requested").count() == 1


def test_claim_is_atomic_one_winner(sa):
    BackupRun.objects.create(requested_by_ref=sa.id)
    first = backup_jobs.claim_next_run()
    assert first is not None and first.status == "running" and first.attempts == 1
    assert backup_jobs.claim_next_run() is None


def test_stale_running_requeued_then_failed_after_limit(sa):
    old = timezone.now() - timedelta(hours=2)
    r = BackupRun.objects.create(
        requested_by_ref=sa.id, status="running", started_at=old, attempts=1
    )
    backup_jobs.recover_stale_runs()
    r.refresh_from_db()
    assert r.status == "queued"
    r.status, r.attempts, r.started_at = "running", backup_jobs.MAX_ATTEMPTS, old
    r.save()
    backup_jobs.recover_stale_runs()
    r.refresh_from_db()
    assert r.status == "failed" and "retry limit" in r.error_message


def _fake_run(monkeypatch, rc, write=None):
    def fake(cmd, **kw):
        if write:
            (backup_jobs.storage_dir() / write[0]).write_text(json.dumps(write[1]))
        return subprocess.CompletedProcess(cmd, rc, stdout="line one\n[FAIL] boom", stderr="")

    monkeypatch.setattr(backup_jobs.subprocess, "run", fake)


def test_exit_zero_without_file_is_failure_not_success(sa, storage, monkeypatch):
    """The script exits 0 when DB credentials are absent. That must never show as a backup."""
    _fake_run(monkeypatch, 0)
    run = backup_jobs.execute_run(
        BackupRun.objects.create(requested_by_ref=sa.id, status="running")
    )
    assert run.status == "failed" and "No backup file" in run.error_message


def test_nonzero_exit_is_failure_with_reason(sa, storage, monkeypatch):
    _fake_run(monkeypatch, 1)
    run = backup_jobs.execute_run(
        BackupRun.objects.create(requested_by_ref=sa.id, status="running")
    )
    assert run.status == "failed" and "boom" in run.error_message


MANIFEST = {
    "format_version": "3.0-envelope-x25519",
    "ciphertext_base64": "AAAA",
    "hmac_signature": "ab",
    "checksum_sha256": "c" * 64,
    "table_count": 3,
    "total_rows": 9,
}
NAME = "tidb_full_backup_20261124_150000.enc.json"


def test_success_records_manifest_and_integrity_check_passes_then_detects_tamper(
    sa, storage, monkeypatch
):
    _fake_run(monkeypatch, 0, write=(NAME, MANIFEST))
    run = backup_jobs.execute_run(
        BackupRun.objects.create(requested_by_ref=sa.id, status="running")
    )
    assert run.status == "succeeded" and run.encryption_mode == "envelope-x25519"
    assert run.total_rows == 9 and (storage / NAME).stat().st_mode & 0o777 == 0o600
    ok, msg = backup_jobs.check_archive_integrity(run)
    assert ok and "not a restore test" in msg
    (storage / NAME).write_text(json.dumps({**MANIFEST, "checksum_sha256": "d" * 64}))
    ok, _ = backup_jobs.check_archive_integrity(run)
    assert not ok


def test_integrity_check_rejects_path_traversal_and_missing(sa, storage):
    run = BackupRun.objects.create(
        requested_by_ref=sa.id, status="succeeded", file_name="../../etc/passwd", byte_size=1
    )
    assert backup_jobs.check_archive_integrity(run)[0] is False
    run.file_name = NAME
    assert backup_jobs.check_archive_integrity(run) == (
        False,
        "Archive file is missing from the storage folder.",
    )


def test_security_page_links_to_backups_only_for_super_admin(client, sa):
    client.force_login(sa)
    assert (
        reverse("accounts:admin_backups")
        in client.get(reverse("accounts:security")).content.decode()
    )


def test_page_does_not_claim_a_restore_function_exists(client, sa, storage):
    client.force_login(sa)
    grant_sudo(sa)
    html = client.get(reverse("accounts:admin_backups")).content.decode()
    assert "There is no restore function yet" in html
    assert "offline by authorised" not in html
