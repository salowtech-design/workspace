"""Backup archive authentication in decrypt_offline_backup.

The consumer authenticates with AES-256-GCM (checksum bound as associated data) plus a SHA-256 payload
check. These tests build a real archive and confirm tampering is rejected."""

import base64
import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.exceptions import PermissionDenied

from apps.accounts.auth.offline_recovery import decrypt_offline_backup


def _make_archive(tmp_path, key, payload):
    raw = json.dumps(payload).encode()
    checksum = hashlib.sha256(raw).hexdigest()
    nonce = os.urandom(12)
    cipher = AESGCM(key).encrypt(nonce, raw, associated_data=checksum.encode())
    manifest = {
        "checksum_sha256": checksum,
        "nonce_base64": base64.b64encode(nonce).decode(),
        "ciphertext_base64": base64.b64encode(cipher).decode(),
        "hmac_signature": "irrelevant-separate-key",
    }
    path = tmp_path / "b.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


def test_valid_archive_round_trips(tmp_path):
    key = os.urandom(32)
    path, _ = _make_archive(tmp_path, key, {"rows": [1, 2, 3]})
    assert decrypt_offline_backup(path, key) == {"rows": [1, 2, 3]}


def test_flipped_ciphertext_byte_rejected(tmp_path):
    key = os.urandom(32)
    path, m = _make_archive(tmp_path, key, {"rows": [1]})
    c = bytearray(base64.b64decode(m["ciphertext_base64"]))
    c[0] ^= 1
    m["ciphertext_base64"] = base64.b64encode(bytes(c)).decode()
    path.write_text(json.dumps(m))
    with pytest.raises(PermissionDenied):
        decrypt_offline_backup(path, key)


def test_tampered_checksum_associated_data_rejected(tmp_path):
    key = os.urandom(32)
    path, m = _make_archive(tmp_path, key, {"rows": [1]})
    m["checksum_sha256"] = "0" * 64
    path.write_text(json.dumps(m))
    with pytest.raises(PermissionDenied):
        decrypt_offline_backup(path, key)


def test_wrong_key_rejected(tmp_path):
    path, _ = _make_archive(tmp_path, os.urandom(32), {"rows": [1]})
    with pytest.raises(PermissionDenied):
        decrypt_offline_backup(path, os.urandom(32))
