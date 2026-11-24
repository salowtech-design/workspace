"""The backup drill must not fall back to hardcoded keys."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "tidb_backup_verify", ROOT / "scripts" / "tidb_backup_verify.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("BACKUP_ENCRYPTION_KEY", "BACKUP_SIGNING_KEY", "AUDIT_HMAC_KEY", "SESSION_HMAC_KEY"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_missing_keys_fail_closed(clean_env):
    mod = _load()
    with pytest.raises(RuntimeError, match="encryption key"):
        mod._get_backup_encryption_key()
    with pytest.raises(RuntimeError, match="signing key"):
        mod._get_backup_signing_key()


def test_configured_keys_are_used_and_independent(clean_env):
    clean_env.setenv("BACKUP_ENCRYPTION_KEY", "enc-test-value")
    clean_env.setenv("BACKUP_SIGNING_KEY", "sig-test-value")
    mod = _load()
    assert len(mod._get_backup_encryption_key()) == 32
    assert mod._get_backup_encryption_key() != mod._get_backup_signing_key()


def test_no_hardcoded_key_strings_remain():
    assert "salow-independent" not in (ROOT / "scripts" / "tidb_backup_verify.py").read_text()
