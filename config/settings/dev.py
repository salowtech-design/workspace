"""
Development settings for Salow Workspace.
"""

import os

import certifi

from .base import *  # noqa: F403

DEBUG = True
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-secret-key-salow-workspace-change-me-in-production-at-least-50-chars",
)

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

# Development verifier allowed in dev
DEV_VERIFIER = os.environ.get("DEV_VERIFIER", "True").lower() in ("true", "1", "yes")

# TiDB Database Integration
ca_path = os.environ.get("TIDB_CA_PATH") or certifi.where()

DATABASES = {
    "default": {
        "ENGINE": "django_tidb",
        "NAME": os.environ.get("TIDB_DB_NAME", "salow_workspace"),
        "USER": os.environ.get("TIDB_USER", "root"),
        "PASSWORD": os.environ.get("TIDB_PASSWORD", ""),
        "HOST": os.environ.get("TIDB_HOST", "127.0.0.1"),
        "PORT": int(os.environ.get("TIDB_PORT", 4000)),
        "OPTIONS": {
            "charset": "utf8mb4",
            "ssl": {
                "ca": ca_path,
            },
        },
    }
}
