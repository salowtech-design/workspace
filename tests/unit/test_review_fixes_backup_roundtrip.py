"""Backup serialisation round trip and the offline recovery console's failure handling."""

import base64
import decimal
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_binary_column_round_trips_through_json_and_a_database():
    """Restore used a hardcoded list of column names, so binary columns outside it (public_key,
    encrypted_totp_secret) came back as base64 text. Bytes are now tagged, whatever the column is called."""
    mod = _load("tidb_backup_verify")
    cols = ["id", "encrypted_totp_secret", "public_key", "amount", "created", "note"]
    row = (
        1,
        os.urandom(40),
        b"\x00\x01\xffpublic",
        decimal.Decimal("12.3400"),
        datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc),
        "plain text",
    )
    payload = json.dumps([mod.serialize_backup_value(v) for v in row])
    back = [mod.deserialize_backup_value(v) for v in json.loads(payload)]
    assert back[1] == row[1] and back[2] == row[2] and isinstance(back[2], bytes)
    assert back[3] == row[3] and back[5] == "plain text"

    db = sqlite3.connect(":memory:")
    db.execute(
        "create table t (id integer, encrypted_totp_secret blob, public_key blob, amount text, created text, note text)"
    )
    db.execute(
        "insert into t values (?,?,?,?,?,?)",
        [back[0], back[1], back[2], str(back[3]), back[4], back[5]],
    )
    got = db.execute("select encrypted_totp_secret, public_key from t").fetchone()
    assert got == (row[1], row[2])
    assert cols  # documents the column set under test


def test_text_that_looks_like_base64_in_a_nonce_named_column_is_not_decoded():
    mod = _load("tidb_backup_verify")
    assert mod.deserialize_backup_value("QUJD") == "QUJD"


def _write_archive(tmp_path, key):
    raw = json.dumps({"tables": {"a": {}}}).encode()
    checksum = hashlib.sha256(raw).hexdigest()
    nonce = os.urandom(12)
    cipher = AESGCM(key).encrypt(nonce, raw, associated_data=checksum.encode())
    path = tmp_path / "x.enc.json"
    path.write_text(
        json.dumps(
            {
                "checksum_sha256": checksum,
                "nonce_base64": base64.b64encode(nonce).decode(),
                "ciphertext_base64": base64.b64encode(cipher).decode(),
            }
        )
    )
    return path


def _run_console(path, key, monkeypatch):
    mod = _load("offline_disaster_recovery")
    shares = mod.split_secret_2_of_3(key)
    answers = iter([shares[0], shares[1]])
    monkeypatch.setattr(mod.getpass, "getpass", lambda *_: next(answers))
    monkeypatch.setattr(sys, "argv", ["x", "--backup-file", str(path), "--dry-run"])
    return mod.main()


def test_console_succeeds_for_a_valid_archive(tmp_path, monkeypatch):
    key = os.urandom(32)
    assert _run_console(_write_archive(tmp_path, key), key, monkeypatch) == 0


def test_console_fails_when_archive_does_not_match_the_key(tmp_path, monkeypatch):
    path = _write_archive(tmp_path, os.urandom(32))
    assert _run_console(path, os.urandom(32), monkeypatch) == 1


def test_console_fails_on_a_tampered_archive(tmp_path, monkeypatch):
    key = os.urandom(32)
    path = _write_archive(tmp_path, key)
    m = json.loads(path.read_text())
    c = bytearray(base64.b64decode(m["ciphertext_base64"]))
    c[3] ^= 1
    m["ciphertext_base64"] = base64.b64encode(bytes(c)).decode()
    path.write_text(json.dumps(m))
    assert _run_console(path, key, monkeypatch) == 1


def test_table_fingerprint_detects_changed_content_not_row_order():
    mod = _load("tidb_backup_verify")
    a = [[1, "x"], [2, "y"]]
    assert mod.table_fingerprint(a) == mod.table_fingerprint(list(reversed(a)))
    assert mod.table_fingerprint(a) != mod.table_fingerprint([[1, "x"], [2, "z"]])
    assert mod.table_fingerprint(a) != mod.table_fingerprint([[1, "x"]])
