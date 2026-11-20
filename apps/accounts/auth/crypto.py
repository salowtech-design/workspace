"""
Cryptographic Core for Salow Workspace & Central IAM.
Provides:
- AES-256-GCM symmetric encryption for database column protection (TOTP secrets, outbox payloads).
- Nonce generation and associated data authentication.
- Multi-key ring support for controlled key rotation drills.
"""

import os
from typing import Optional, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from apps.accounts.auth.config import get_biometric_key_ring_dict
from apps.accounts.auth.errors import AuthException
from apps.face.engine.crypto import BiometricKeyRing


def get_auth_key_ring() -> BiometricKeyRing:
    """Retrieve key ring for symmetric secret encryption at rest."""
    keys = get_biometric_key_ring_dict()
    active_key_id = os.getenv("BIOMETRIC_ACTIVE_KEY_ID", "k1")
    return BiometricKeyRing(keys=keys, active_key_id=active_key_id)


def get_key(key_id: str) -> bytes:
    """Retrieves specific key by key_id from key ring."""
    ring = get_auth_key_ring()
    return ring.get_key(key_id)


def encrypt_totp_secret(
    secret_base32: str,
    key_id: Optional[str] = None,
) -> Tuple[bytes, bytes, str]:
    """Encrypts a base32 TOTP secret using AES-256-GCM with a fresh 96-bit nonce."""
    ring = get_auth_key_ring()
    active_key_id = key_id or ring.active_key_id
    key = ring.get_key(active_key_id)
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(
        nonce, secret_base32.encode("utf-8"), associated_data=active_key_id.encode("utf-8")
    )
    return ciphertext, nonce, active_key_id


def decrypt_totp_secret(
    encrypted_secret: bytes,
    nonce: bytes,
    key_id: str,
) -> str:
    """Decrypts an AES-256-GCM encrypted TOTP secret."""
    ring = get_auth_key_ring()
    key = ring.get_key(key_id)
    aesgcm = AESGCM(key)
    try:
        raw = aesgcm.decrypt(nonce, encrypted_secret, associated_data=key_id.encode("utf-8"))
        return raw.decode("utf-8")
    except InvalidTag:
        raise AuthException(
            "AUTH-MFA-001",
            step="decrypt_secret",
            message_override="Failed to decrypt secret. Key ring mismatch or corrupted record.",
        )
