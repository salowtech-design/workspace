"""
Test settings for Salow Workspace.
"""

import os

from .base import *  # noqa: F403
from .base import BASE_DIR

DEBUG = False
SECRET_KEY = "test-secret-key-salow-workspace-for-automated-testing-suite"
ALLOWED_HOSTS = ["*"]

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
    }
}
