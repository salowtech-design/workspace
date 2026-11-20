"""
Production settings for Salow Workspace.
Strict fail-closed security configuration.
"""

import os

import certifi
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False

# Production MUST NOT allow the development stub verifier
DEV_VERIFIER = False
if os.environ.get("DEV_VERIFIER", "false").lower() in ("true", "1", "yes"):
    raise ImproperlyConfigured("DEV_VERIFIER cannot be enabled in production environments.")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY or len(SECRET_KEY) < 50:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set in production and be at least 50 characters long."
    )

# Independent Audit Signing Key Custody (Subsystem 1 — strictly separated from SECRET_KEY)
raw_audit_key = os.environ.get("AUDIT_HMAC_KEY")
if not raw_audit_key or len(raw_audit_key) < 32:
    raise ImproperlyConfigured(
        "AUDIT_HMAC_KEY must be independently configured in production and be at least 32 characters long."
    )
if raw_audit_key == SECRET_KEY:
    raise ImproperlyConfigured(
        "AUDIT_HMAC_KEY cannot match DJANGO_SECRET_KEY. Audit signing keys must be independently managed."
    )
AUDIT_HMAC_KEY = raw_audit_key
AUDIT_ACTIVE_KEY_ID = os.environ.get("AUDIT_ACTIVE_KEY_ID", "k1")
AUDIT_SIGNING_KEYS = {
    AUDIT_ACTIVE_KEY_ID: raw_audit_key,
}
audit_checkpoint_path = os.environ.get("AUDIT_CHECKPOINT_DIR")
if not audit_checkpoint_path:
    AUDIT_CHECKPOINT_DIR = BASE_DIR / "var" / "audit_checkpoints"
else:
    AUDIT_CHECKPOINT_DIR = Path(audit_checkpoint_path)

raw_allowed_hosts = os.environ.get("ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [h.strip() for h in raw_allowed_hosts.split(",") if h.strip()]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be explicitly defined in production.")

# TiDB Database Integration
ca_path = os.environ.get("TIDB_CA_PATH") or certifi.where()

DATABASES = {
    "default": {
        "ENGINE": "django_tidb",
        "NAME": os.environ.get("TIDB_DB_NAME", "salow_workspace"),
        "USER": os.environ.get("TIDB_USER"),
        "PASSWORD": os.environ.get("TIDB_PASSWORD"),
        "HOST": os.environ.get("TIDB_HOST"),
        "PORT": int(os.environ.get("TIDB_PORT", 4000)),
        "OPTIONS": {
            "charset": "utf8mb4",
            "ssl": {
                "ca": ca_path,
            },
        },
    }
}

# Strict Cookie Security (Contract v1.0 Section 4)
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_AGE = 43200  # 12 hours baseline
SESSION_COOKIE_NAME = "__Host-salow_workspace_session"
IAM_SESSION_COOKIE_NAME = "__Host-salow_iam_session"

CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_NAME = "__Host-csrftoken"

# Reverse Proxy & SSL Offloading Security
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# Security Headers
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True
