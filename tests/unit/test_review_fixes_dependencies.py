"""Regression tests for dependency declaration and undefined-name fixes."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pymysql_declared_because_config_imports_it():
    assert "import pymysql" in (ROOT / "config" / "__init__.py").read_text()
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    assert any(d.lower().startswith("pymysql") for d in deps)


def test_pymysql_present_in_lock_file():
    assert 'name = "pymysql"' in (ROOT / "uv.lock").read_text()


def test_compute_payload_digest_serialises_dicts_and_lists():
    """iam_auth used json without importing it, so dict payloads raised NameError."""
    from apps.accounts.auth.iam_auth import compute_payload_digest

    a = compute_payload_digest({"b": 1, "a": [1, 2]})
    b = compute_payload_digest({"a": [1, 2], "b": 1})
    assert a == b and len(a) == 64
    assert compute_payload_digest(["x"]) != compute_payload_digest(["y"])
    assert compute_payload_digest("plain") == compute_payload_digest("plain")


def test_decrypt_offline_backup_wrong_key_raises_permission_denied(tmp_path):
    """A wrong reconstructed key must raise PermissionDenied, not NameError."""
    import base64
    import hashlib
    import hmac
    import json
    import os

    import pytest
    from django.core.exceptions import PermissionDenied

    from apps.accounts.auth.offline_recovery import decrypt_offline_backup

    key = os.urandom(32)
    cipher = os.urandom(48)  # not valid AES-GCM output for this key
    sig_key = hashlib.sha256(key + b"-signing-seal").digest()
    manifest = {
        "ciphertext_base64": base64.b64encode(cipher).decode(),
        "nonce_base64": base64.b64encode(os.urandom(12)).decode(),
        "checksum_sha256": "0" * 64,
        "hmac_signature": hmac.new(sig_key, cipher, hashlib.sha256).hexdigest(),
    }
    path = tmp_path / "backup.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(PermissionDenied):
        decrypt_offline_backup(path, key)


def test_control_action_view_logs_unexpected_error_instead_of_nameerror(caplog):
    """An unexpected backend error must be logged and shown, not crash on an undefined logger."""
    import logging
    from types import SimpleNamespace
    from unittest import mock

    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    from apps.accounts.views import authentication_centre as view_mod

    request = RequestFactory().post(
        "/iam/auth/action/", {"action_type": "revoke_session", "session_ref": "x"}
    )
    request.session = {}
    request._messages = FallbackStorage(request)
    fake_session = SimpleNamespace(iam_admin=object())
    with (
        mock.patch.object(
            view_mod, "_require_iam_admin_session", return_value=(fake_session, None)
        ),
        mock.patch.object(view_mod, "execute_revoke_session", side_effect=RuntimeError("boom")),
        caplog.at_level(logging.ERROR),
    ):
        response = view_mod.auth_control_action_view(request)
    assert response.status_code == 302
    assert any("revoke_session" in r.getMessage() for r in caplog.records)
    assert any("Operation failed" in str(m) for m in request._messages)
