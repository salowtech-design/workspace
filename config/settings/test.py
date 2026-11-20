"""
Test settings for Salow Workspace.
"""

import os

from .base import *  # noqa: F403
from .base import BASE_DIR

DEBUG = False
TESTING = True
SECRET_KEY = "test-secret-key-salow-workspace-for-automated-testing-suite"
ALLOWED_HOSTS = ["*"]

# Test Cryptographic Keys
BIOMETRIC_PRIMARY_KEY_HEX = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
ACTIVATION_HMAC_KEY = "test-activation-hmac-key-32bytes-secret-val!"
EMAIL_PAYLOAD_KEY_HEX = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
SESSION_HMAC_KEY = "test-session-hmac-key-32bytes-secret-val-x!"

# Independent Audit Signing Key Custody (strictly separated from SECRET_KEY)
AUDIT_HMAC_KEY = "independent-audit-signing-key-test-custody-32bytes-secret"
AUDIT_SIGNING_KEYS = {
    "k1": "independent-audit-signing-key-test-custody-32bytes-secret",
    "k2": "independent-audit-signing-key-rotated-32bytes-secret",
}
AUDIT_ACTIVE_KEY_ID = "k1"
AUDIT_CHECKPOINT_DIR = BASE_DIR / "var" / "test_audit_checkpoints"

DEV_VERIFIER = True

# Password hashers: Argon2id primary as required by architectural authority
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Database configuration: defaults to persistent test sqlite file for CLI commands,
# or in-memory when TEST_DB_NAME=":memory:".
db_name = os.environ.get("TEST_DB_NAME", str(BASE_DIR / "test_db.sqlite3"))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": db_name,
        "OPTIONS": {
            "timeout": 30,
        },
    }
}

from django.db.backends.signals import connection_created
from django.dispatch import receiver


@receiver(connection_created)
def _configure_sqlite_wal(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        try:
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=30000;")
        except Exception:
            pass

