"""
Biometric Template Cryptography Module.
Implements authenticated AES-256-GCM encryption for face feature vectors.
Provides key rotation, fresh 96-bit nonces, and authenticated integrity checks.
"""

import os
import struct
from typing import NamedTuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptedTemplate(NamedTuple):
    key_id: str
    nonce: bytes
    ciphertext: bytes


class BiometricKeyRing:
    """Manages versioned 256-bit AES keys for biometric template encryption."""

    def __init__(self, keys: dict[str, bytes], active_key_id: str):
        if not keys or active_key_id not in keys:
            raise ValueError(f"Active key ID '{active_key_id}' must exist in keys dict.")
        for k_id, k_val in keys.items():
            if len(k_val) != 32:
                raise ValueError(f"Key '{k_id}' must be exactly 32 bytes (256-bit).")

        self._keys = keys.copy()
        self.active_key_id = active_key_id

    def get_key(self, key_id: str) -> bytes:
        if key_id not in self._keys:
            raise KeyError(f"Key ID '{key_id}' not found in key ring.")
        return self._keys[key_id]

    @property
    def active_key(self) -> bytes:
        return self._keys[self.active_key_id]


def serialize_embedding(embedding: list[float]) -> bytes:
    """Serialize list of 128 floats (float32) into binary."""
    return struct.pack(f"<{len(embedding)}f", *embedding)


def deserialize_embedding(data: bytes) -> list[float]:
    """Deserialize binary data back to list of floats."""
    count = len(data) // 4
    return list(struct.unpack(f"<{count}f", data))


def encrypt_template(embedding: list[float], key_ring: BiometricKeyRing) -> bytes:
    """
    Encrypt face embedding using AES-256-GCM with a fresh 96-bit nonce.
    Format: [1-byte key_id_len][key_id (ascii)][12-byte nonce][ciphertext + 16-byte tag]
    """
    key_id_bytes = key_ring.active_key_id.encode("utf-8")
    if len(key_id_bytes) > 255:
        raise ValueError("Key ID length cannot exceed 255 bytes.")

    nonce = os.urandom(12)  # 96-bit nonce
    aesgcm = AESGCM(key_ring.active_key)

    raw_payload = serialize_embedding(embedding)
    ciphertext = aesgcm.encrypt(nonce, raw_payload, associated_data=key_id_bytes)

    header = struct.pack("!B", len(key_id_bytes)) + key_id_bytes + nonce
    return header + ciphertext


def decrypt_template(encrypted_blob: bytes, key_ring: BiometricKeyRing) -> list[float]:
    """
    Decrypt and verify integrity of an AES-256-GCM encrypted biometric template.
    Raises InvalidTag if ciphertext or associated data was modified.
    """
    if len(encrypted_blob) < 1 + 12 + 16:
        raise ValueError("Encrypted template blob is too short.")

    key_id_len = struct.unpack("!B", encrypted_blob[:1])[0]
    key_id_end = 1 + key_id_len
    key_id = encrypted_blob[1:key_id_end].decode("utf-8")

    nonce_end = key_id_end + 12
    nonce = encrypted_blob[key_id_end:nonce_end]
    ciphertext = encrypted_blob[nonce_end:]

    key_bytes = key_ring.get_key(key_id)
    aesgcm = AESGCM(key_bytes)

    try:
        decrypted_raw = aesgcm.decrypt(nonce, ciphertext, associated_data=key_id.encode("utf-8"))
    except InvalidTag as e:
        raise ValueError(
            "Biometric template integrity check failed: ciphertext was tampered."
        ) from e

    return deserialize_embedding(decrypted_raw)
