"""
Authentication Configuration & Settings Loader.
Reads environment settings, applies safe defaults, and validates operational parameters.
"""

import hashlib
import json
import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def get_activation_hmac_key() -> bytes:
    """Derive or load the HMAC secret key for one-time code hashing."""
    configured = os.getenv("ACTIVATION_HMAC_KEY") or getattr(settings, "ACTIVATION_HMAC_KEY", None)
    if configured:
        return configured.encode("utf-8") if isinstance(configured, str) else configured
    if not getattr(settings, "DEBUG", True) and not getattr(settings, "TESTING", False):
        raise ImproperlyConfigured(
            "ACTIVATION_HMAC_KEY must be explicitly configured in production."
        )
    # Isolated development fallback using project secret key
    secret = getattr(settings, "SECRET_KEY", "fallback-secret-key-for-development-mode-testing")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def get_session_hmac_key() -> bytes:
    """Derive or load the HMAC secret key for session verifier calculation."""
    configured = os.getenv("SESSION_HMAC_KEY") or getattr(settings, "SESSION_HMAC_KEY", None)
    if configured:
        return configured.encode("utf-8") if isinstance(configured, str) else configured
    if not getattr(settings, "DEBUG", True) and not getattr(settings, "TESTING", False):
        raise ImproperlyConfigured("SESSION_HMAC_KEY must be explicitly configured in production.")
    secret = getattr(settings, "SECRET_KEY", "fallback-secret-for-sessions-dev")
    return hashlib.sha256(b"SalowWorkspaceSessionHMAC:" + secret.encode("utf-8")).digest()


def get_biometric_key_ring_dict() -> dict[str, bytes]:
    """Retrieve active 256-bit AES keys for template and TOTP secret encryption."""
    ring_json = os.getenv("BIOMETRIC_KEY_RING_JSON")
    if ring_json:
        try:
            raw_dict = json.loads(ring_json)
            return {k: bytes.fromhex(v) for k, v in raw_dict.items()}
        except Exception as e:
            raise ImproperlyConfigured(f"Invalid BIOMETRIC_KEY_RING_JSON: {e}") from e

    primary_key_hex = os.getenv("BIOMETRIC_PRIMARY_KEY_HEX") or getattr(
        settings, "BIOMETRIC_PRIMARY_KEY_HEX", None
    )
    if primary_key_hex and len(primary_key_hex) == 64:
        secondary_hex = os.getenv("BIOMETRIC_SECONDARY_KEY_HEX") or getattr(
            settings, "BIOMETRIC_SECONDARY_KEY_HEX", None
        )
        res = {"k1": bytes.fromhex(primary_key_hex)}
        if secondary_hex and len(secondary_hex) == 64:
            res["k2"] = bytes.fromhex(secondary_hex)
        return res

    if not getattr(settings, "DEBUG", True) and not getattr(settings, "TESTING", False):
        raise ImproperlyConfigured(
            "BIOMETRIC_PRIMARY_KEY_HEX must be explicitly configured in production."
        )

    fallback_key = hashlib.sha256(b"SalowWorkspaceBiometricKeyRingDevOnly2026").digest()
    return {"k1": fallback_key}


def get_email_key_ring_dict() -> dict[str, bytes]:
    """Retrieve active 256-bit AES keys for transactional email payload encryption."""
    ring_json = os.getenv("EMAIL_KEY_RING_JSON")
    if ring_json:
        try:
            raw_dict = json.loads(ring_json)
            return {k: bytes.fromhex(v) for k, v in raw_dict.items()}
        except Exception as e:
            raise ImproperlyConfigured(f"Invalid EMAIL_KEY_RING_JSON: {e}") from e

    primary_key_hex = os.getenv("EMAIL_PAYLOAD_KEY_HEX") or getattr(
        settings, "EMAIL_PAYLOAD_KEY_HEX", None
    )
    if primary_key_hex and len(primary_key_hex) == 64:
        secondary_hex = os.getenv("EMAIL_PAYLOAD_SECONDARY_KEY_HEX") or getattr(
            settings, "EMAIL_PAYLOAD_SECONDARY_KEY_HEX", None
        )
        res = {"k1": bytes.fromhex(primary_key_hex)}
        if secondary_hex and len(secondary_hex) == 64:
            res["k2"] = bytes.fromhex(secondary_hex)
        return res

    if not getattr(settings, "DEBUG", True) and not getattr(settings, "TESTING", False):
        raise ImproperlyConfigured(
            "EMAIL_PAYLOAD_KEY_HEX must be explicitly configured in production."
        )

    fallback_key = hashlib.sha256(b"SalowWorkspaceEmailKeyRingDevOnly2026").digest()
    return {"k1": fallback_key}


# Operational limits
ACTIVATION_TTL_HOURS = int(os.getenv("AUTH_ACTIVATION_TTL_HOURS", "48"))
MAX_ACTIVATION_ATTEMPTS = 5
ACTIVATION_LOCKOUT_THRESHOLD = 5
RETURNING_SIGNIN_LOCKOUT_THRESHOLD = 3
LOCKOUT_THRESHOLD = 5  # Default fallback
LOCKOUT_DURATION_MINUTES = 15
SUDO_WINDOW_MINUTES = 10
STAFF_SESSION_MAX_HOURS = 12

# Company email domain configuration
ALLOWED_EMAIL_DOMAINS = [
    d.strip().lower()
    for d in os.getenv("ALLOWED_EMAIL_DOMAINS", "salowtech.com").split(",")
    if d.strip()
]


def is_allowed_company_email(email: str) -> bool:
    """Validate whether an email address belongs to an allowed company domain."""
    if not email or "@" not in email:
        return False
    domain = email.strip().split("@")[-1].lower()
    return any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)
