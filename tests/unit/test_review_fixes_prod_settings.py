"""Production settings must reject key values that are published in the repository."""

import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env(**overrides):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME")}
    env.update(
        DJANGO_SETTINGS_MODULE="config.settings.prod",
        DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
        AUDIT_HMAC_KEY=secrets.token_urlsafe(40),
        BIOMETRIC_PRIMARY_KEY_HEX=secrets.token_hex(32),
        ACTIVATION_HMAC_KEY=secrets.token_urlsafe(40),
        EMAIL_PAYLOAD_KEY_HEX=secrets.token_hex(32),
        SESSION_HMAC_KEY=secrets.token_urlsafe(40),
        AUDIT_CHECKPOINT_DIR="/tmp/salow-test-checkpoints",  # noqa: S108 (fixture path, never written)
        ALLOWED_HOSTS="example.com",
        TIDB_USER="u",
        TIDB_PASSWORD="p",
        TIDB_HOST="h",
    )
    env.update(overrides)
    return env


def _import_prod(**overrides):
    return subprocess.run(
        [sys.executable, "-c", "import importlib; importlib.import_module('config.settings.prod')"],
        env=_env(**overrides),
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def _example_value(name):
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"{name} missing from .env.example")


def test_prod_settings_accept_fresh_random_keys():
    result = _import_prod()
    assert result.returncode == 0, result.stderr[-500:]


def test_prod_rejects_audit_key_copied_from_env_example():
    result = _import_prod(AUDIT_HMAC_KEY=_example_value("AUDIT_HMAC_KEY"))
    assert result.returncode != 0
    assert "published in the repository" in result.stderr


def test_prod_rejects_secret_key_placeholder_from_env_example():
    placeholder = _example_value("DJANGO_SECRET_KEY")
    placeholder = placeholder + "x" * max(0, 60 - len(placeholder))
    result = _import_prod(DJANGO_SECRET_KEY=placeholder)
    assert result.returncode != 0
    assert "placeholder" in result.stderr
