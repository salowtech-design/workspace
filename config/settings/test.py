"""
Test settings for Salow Workspace.
"""

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-secret-key-salow-workspace-for-automated-testing-suite"
ALLOWED_HOSTS = ["*"]

DEV_VERIFIER = True

# Fast password hasher for test execution speed
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# In-memory database for isolated pure unit tests
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
