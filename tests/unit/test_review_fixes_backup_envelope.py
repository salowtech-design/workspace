"""Envelope-encrypted backups: the backup job holds only a public key; 2-of-3 custodians can recover."""

import base64
import json
import os

import pytest
from django.core.exceptions import PermissionDenied

from apps.accounts.auth.offline_recovery import (
    decrypt_offline_backup,
    encrypt_backup_envelope,
    parse_share,
    reconstruct_secret_2_of_3,
    recovery_public_key_from_master,
    split_secret_2_of_3,
)
from tests.unit.test_review_fixes_backup_roundtrip import _load


def _archive(tmp_path, master, payload=b'{"tables": {"t": {"rows": [1]}}}'):
    manifest, dek = encrypt_backup_envelope(payload, recovery_public_key_from_master(master))
    path = tmp_path / "e.enc.json"
    path.write_text(json.dumps(manifest))
    return path, manifest, dek


def test_two_custodian_shares_recover_the_archive(tmp_path):
    master = os.urandom(32)
    shares = split_secret_2_of_3(master)
    path, _, _ = _archive(tmp_path, master)
    for pair in ((0, 1), (1, 2), (0, 2)):
        key = reconstruct_secret_2_of_3([parse_share(shares[i]) for i in pair])
        assert decrypt_offline_backup(path, key) == {"tables": {"t": {"rows": [1]}}}


def test_backup_job_key_material_cannot_decrypt_old_archives(tmp_path):
    master = os.urandom(32)
    path, _, dek = _archive(tmp_path, master)
    pub = recovery_public_key_from_master(master)
    with pytest.raises(PermissionDenied):
        decrypt_offline_backup(path, pub)  # the public key is not the private key
    with pytest.raises(PermissionDenied):
        decrypt_offline_backup(path, dek)


def test_wrong_custodian_key_rejected(tmp_path):
    path, _, _ = _archive(tmp_path, os.urandom(32))
    with pytest.raises(PermissionDenied):
        decrypt_offline_backup(path, os.urandom(32))


@pytest.mark.parametrize(
    "field", ["wrapped_dek_base64", "ciphertext_base64", "ephemeral_public_key_base64"]
)
def test_tampering_with_any_envelope_part_is_rejected(tmp_path, field):
    master = os.urandom(32)
    path, manifest, _ = _archive(tmp_path, master)
    raw = bytearray(base64.b64decode(manifest[field]))
    raw[0] ^= 1
    manifest[field] = base64.b64encode(bytes(raw)).decode()
    path.write_text(json.dumps(manifest))
    with pytest.raises(PermissionDenied):
        decrypt_offline_backup(path, master)


def test_changed_checksum_rejected(tmp_path):
    master = os.urandom(32)
    path, manifest, _ = _archive(tmp_path, master)
    manifest["checksum_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(PermissionDenied):
        decrypt_offline_backup(path, master)


def test_each_backup_uses_a_fresh_data_key(tmp_path):
    master = os.urandom(32)
    _, m1, d1 = _archive(tmp_path, master)
    _, m2, d2 = _archive(tmp_path, master)
    assert d1 != d2 and m1["ciphertext_base64"] != m2["ciphertext_base64"]


def test_one_secret_never_gives_the_same_encryption_and_signing_key(monkeypatch):
    for k in ("BACKUP_ENCRYPTION_KEY", "BACKUP_SIGNING_KEY", "SESSION_HMAC_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AUDIT_HMAC_KEY", "same-secret-for-both")
    mod = _load("tidb_backup_verify")
    assert mod._get_backup_encryption_key() != mod._get_backup_signing_key()
    assert len(mod._get_backup_encryption_key()) == 32


def test_envelope_mode_needs_no_encryption_key_but_validates_the_public_key(monkeypatch):
    for k in ("BACKUP_ENCRYPTION_KEY", "AUDIT_HMAC_KEY", "SESSION_HMAC_KEY"):
        monkeypatch.delenv(k, raising=False)
    mod = _load("tidb_backup_verify")
    pub = recovery_public_key_from_master(os.urandom(32))
    monkeypatch.setenv("BACKUP_RECOVERY_PUBLIC_KEY", base64.b64encode(pub).decode())
    assert mod._get_recovery_public_key() == pub
    monkeypatch.setenv("BACKUP_RECOVERY_PUBLIC_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(RuntimeError):
        mod._get_recovery_public_key()
