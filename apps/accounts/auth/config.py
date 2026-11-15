"""
Authentication Configuration & Settings Loader.
Reads environment settings, applies safe defaults, and validates operational parameters.
"""

import hashlib
import os

from django.conf import settings


def get_activation_hmac_key() -> bytes:
    """Derive or load the HMAC secret key for one-time code hashing."""
    configured = os.getenv("ACTIVATION_HMAC_KEY")
    if configured:
        return configured.encode("utf-8")
    # Deterministic fallback using project secret key
    secret = getattr(settings, "SECRET_KEY", "fallback-secret-key-for-development-mode-testing")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def get_biometric_key_ring_dict() -> dict[str, bytes]:
    """Retrieve active 256-bit AES keys for template and TOTP secret encryption."""
    primary_key_hex = os.getenv("BIOMETRIC_PRIMARY_KEY_HEX")
    if primary_key_hex and len(primary_key_hex) == 64:
        return {"k1": bytes.fromhex(primary_key_hex)}

    # Default development/test 256-bit key
    fallback_key = hashlib.sha256(b"SalowWorkspaceBiometricKeyRingDefault2026").digest()
    return {"k1": fallback_key}


# Operational limits
ACTIVATION_TTL_HOURS = int(os.getenv("AUTH_ACTIVATION_TTL_HOURS", "48"))
MAX_ACTIVATION_ATTEMPTS = 5
LOCKOUT_THRESHOLD = 5
LOCKOUT_DURATION_MINUTES = 15
SUDO_WINDOW_MINUTES = 10
STAFF_SESSION_MAX_HOURS = 12
