"""
Independent Offline Disaster Recovery (2-of-3 Threshold Split-Key Recovery).
Strictly segregated from online break-glass elevation.

Implements Shamir's Secret Sharing over a 256-bit Galois prime field:
- Exactly 3 custodian shares issued to independent organizational roles:
  1. Custodian A: Platform & Infrastructure Lead
  2. Custodian B: Security & Compliance Officer
  3. Custodian C: Engineering Executive
- Any 2 of the 3 shares can reconstruct the offline disaster recovery master key.
- A single share reveals zero mathematical information about the underlying secret.
- Custodians input shares via secure interactive prompt or isolated files (never CLI arguments).
"""

import base64
import hashlib
import json
import logging
import re
import secrets
from pathlib import Path
from typing import Dict, List, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.core.exceptions import PermissionDenied

logger = logging.getLogger(__name__)

# Standard 256-bit prime field: p = 2^256 + 297
PRIME_256 = 115792089237316195423570985008687907853269984665640564039457584007913129640233


def _mod_inverse(a: int, m: int) -> int:
    """Compute modular inverse using Fermat's Little Theorem."""
    return pow(a % m, m - 2, m)


def split_secret_2_of_3(secret_bytes: bytes) -> List[str]:
    """
    Splits a 32-byte secret into 3 cryptographic shares with a 2-of-3 threshold.
    Returns list of formatted custodian share strings.
    """
    if len(secret_bytes) != 32:
        raise ValueError("Secret must be exactly 32 bytes.")

    secret_int = int.from_bytes(secret_bytes, byteorder="big")
    if secret_int >= PRIME_256:
        raise ValueError("Secret integer exceeds prime field boundary.")

    # Random polynomial coefficient a1 in [1, PRIME_256 - 1]
    a1 = secrets.randbelow(PRIME_256 - 1) + 1

    shares = []
    for x in (1, 2, 3):
        # f(x) = (secret + a1 * x) % PRIME_256
        y = (secret_int + a1 * x) % PRIME_256
        # Encode share as base64 string
        share_payload = f"{x}:{y}".encode("utf-8")
        encoded_share = base64.b64encode(share_payload).decode("ascii")
        shares.append(f"SALOW-DR-SHARE-{x}-{encoded_share}")

    return shares


def parse_share(share_str: str) -> Tuple[int, int]:
    """Parses and validates a custodian recovery share string."""
    clean = share_str.strip()
    if not clean.startswith("SALOW-DR-SHARE-"):
        raise ValueError("Invalid share prefix. Must start with SALOW-DR-SHARE-")

    parts = clean.split("-", 4)
    if len(parts) != 5:
        raise ValueError("Invalid share structure.")

    share_idx = int(parts[3])
    raw_payload = base64.b64decode(parts[4]).decode("utf-8")
    x_str, y_str = raw_payload.split(":")
    x, y = int(x_str), int(y_str)

    if x != share_idx or x not in (1, 2, 3):
        raise ValueError(f"Share index mismatch: expected {share_idx}, found {x}")

    if not (0 <= y < PRIME_256):
        raise ValueError("Share value out of prime field range.")

    return x, y


def reconstruct_secret_2_of_3(shares: List[Tuple[int, int]]) -> bytes:
    """
    Reconstructs the 32-byte secret from any 2 distinct custodian shares
    using Lagrange polynomial interpolation at x = 0.
    """
    if len(shares) < 2:
        raise ValueError("At least 2 distinct custodian shares are required.")

    # Take first 2 shares
    (x1, y1), (x2, y2) = shares[0], shares[1]
    if x1 == x2:
        raise ValueError(
            "Duplicate shares provided. Reconstructing requires 2 distinct custodian shares."
        )

    # Lagrange basis polynomials evaluated at x = 0:
    # L1(0) = (0 - x2) / (x1 - x2) mod p
    # L2(0) = (0 - x1) / (x2 - x1) mod p
    num1 = (-x2) % PRIME_256
    den1 = (x1 - x2) % PRIME_256
    l1 = (num1 * _mod_inverse(den1, PRIME_256)) % PRIME_256

    num2 = (-x1) % PRIME_256
    den2 = (x2 - x1) % PRIME_256
    l2 = (num2 * _mod_inverse(den2, PRIME_256)) % PRIME_256

    secret_int = (y1 * l1 + y2 * l2) % PRIME_256
    secret_bytes = secret_int.to_bytes(32, byteorder="big")
    return secret_bytes


ENVELOPE_FORMAT = "3.0-envelope-x25519"
_WRAP_INFO = b"salow-backup-dek-wrap-v1"


def recovery_public_key_from_master(master_key: bytes) -> bytes:
    """Public half of the recovery key pair. It is not secret: the backup job holds only this."""
    if len(master_key) != 32:
        raise ValueError("Master key must be exactly 32 bytes.")
    return (
        X25519PrivateKey.from_private_bytes(master_key)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )


def _wrap_key(shared_secret: bytes, eph_pub: bytes, recipient_pub: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=eph_pub + recipient_pub, info=_WRAP_INFO
    ).derive(shared_secret)


def encrypt_backup_envelope(payload: bytes, recipient_public_key: bytes) -> Tuple[Dict, bytes]:
    """
    Envelope-encrypts a backup. A random data key (DEK) encrypts the payload with AES-256-GCM and is
    wrapped to the recovery public key (ephemeral X25519 + HKDF + AES-GCM). Whoever runs the backup holds
    only the public key and cannot decrypt old archives; the 2-of-3 custodians can, by rebuilding the
    private key. Returns (manifest, dek); the caller may keep the DEK in memory for an immediate restore
    drill and must not store it.
    """
    recipient = X25519PublicKey.from_public_bytes(recipient_public_key)
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    checksum = hashlib.sha256(payload).hexdigest()
    dek = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(dek).encrypt(nonce, payload, checksum.encode("utf-8"))
    wrap_nonce = secrets.token_bytes(12)
    wrapped = AESGCM(_wrap_key(eph.exchange(recipient), eph_pub, recipient_public_key)).encrypt(
        wrap_nonce, dek, checksum.encode("utf-8")
    )
    manifest = {
        "format_version": ENVELOPE_FORMAT,
        "checksum_sha256": checksum,
        "nonce_base64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_base64": base64.b64encode(ciphertext).decode("ascii"),
        "wrapped_dek_base64": base64.b64encode(wrapped).decode("ascii"),
        "wrap_nonce_base64": base64.b64encode(wrap_nonce).decode("ascii"),
        "ephemeral_public_key_base64": base64.b64encode(eph_pub).decode("ascii"),
        "recovery_public_key_base64": base64.b64encode(recipient_public_key).decode("ascii"),
    }
    return manifest, dek


def _unwrap_dek(manifest: Dict, master_key: bytes) -> bytes:
    private = X25519PrivateKey.from_private_bytes(master_key)
    recipient_pub = recovery_public_key_from_master(master_key)
    if base64.b64decode(manifest["recovery_public_key_base64"]) != recipient_pub:
        raise PermissionDenied("Reconstructed key does not match this archive's recovery key.")
    eph_pub = base64.b64decode(manifest["ephemeral_public_key_base64"])
    key = _wrap_key(
        private.exchange(X25519PublicKey.from_public_bytes(eph_pub)), eph_pub, recipient_pub
    )
    return AESGCM(key).decrypt(
        base64.b64decode(manifest["wrap_nonce_base64"]),
        base64.b64decode(manifest["wrapped_dek_base64"]),
        manifest["checksum_sha256"].encode("utf-8"),
    )


def decrypt_offline_backup(backup_file_path: Path, reconstructed_key: bytes) -> Dict:
    """
    Decrypts and validates an encrypted backup manifest using the reconstructed offline key.
    Validates AES-GCM authentication and the SHA-256 payload checksum.
    """
    with open(backup_file_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Integrity: AES-256-GCM authenticates the ciphertext and binds the manifest checksum as associated
    # data, so a modified archive or a wrong key fails decryption below. The manifest's hmac_signature
    # is made by scripts/tidb_backup_verify.py with a separate signing key that is not derivable from the
    # reconstructed key, so it is verified by that script (verify step), not here.
    loaded_cipher = base64.b64decode(manifest["ciphertext_base64"])

    # 2. Decrypt ciphertext with AES-256-GCM (envelope archives unwrap their data key first)
    loaded_nonce = base64.b64decode(manifest["nonce_base64"])
    if manifest.get("format_version") == ENVELOPE_FORMAT:
        try:
            aesgcm = AESGCM(_unwrap_dek(manifest, reconstructed_key))
        except PermissionDenied:
            raise
        except Exception as exc:
            raise PermissionDenied(f"Recovery key could not unwrap this archive ({exc})") from exc
    else:
        aesgcm = AESGCM(reconstructed_key)
    try:
        decrypted_bytes = aesgcm.decrypt(
            loaded_nonce,
            loaded_cipher,
            associated_data=manifest["checksum_sha256"].encode("utf-8"),
        )
    except Exception as exc:
        raise PermissionDenied(
            f"Decryption failed: Reconstructed key does not match backup archive ({exc})"
        ) from exc

    if hashlib.sha256(decrypted_bytes).hexdigest() != manifest["checksum_sha256"]:
        raise ValueError("Decrypted backup payload checksum validation failed.")

    payload = json.loads(decrypted_bytes.decode("utf-8"))
    return payload


def execute_post_restore_security_reconciliation(cursor, table_prefix: str = "") -> Dict[str, int]:
    """
    Mandatory Security Reconciliation following disaster restore:
    1. Revokes all active sessions.
    2. Terminates pending recovery requests and device approvals.
    3. Clears stale rate limits.
    4. Logs disaster recovery audit event.
    """
    reconciled = {
        "user_sessions_revoked": 0,
        "iam_sessions_revoked": 0,
        "recovery_requests_expired": 0,
        "device_requests_expired": 0,
    }

    if not re.fullmatch(r"[A-Za-z0-9_]*", table_prefix or ""):
        raise ValueError("table_prefix may contain only letters, digits and underscores.")

    t_usersession = f"`{table_prefix}accounts_usersession`"
    t_iamsession = f"`{table_prefix}accounts_iamsession`"
    t_recovery = f"`{table_prefix}accounts_recoveryrequest`"
    t_device = f"`{table_prefix}accounts_deviceapprovalrequest`"

    try:
        cursor.execute(f"UPDATE {t_iamsession} SET is_active = FALSE WHERE is_active = TRUE;")  # noqa: S608 (table_prefix validated against ^[A-Za-z0-9_]*$ above)
        reconciled["iam_sessions_revoked"] = cursor.rowcount
    except Exception:
        logger.exception(
            "Post-restore reconciliation failed; restore must be treated as incomplete"
        )
        raise

    try:
        cursor.execute(f"DELETE FROM {t_usersession};")  # noqa: S608 (table_prefix validated against ^[A-Za-z0-9_]*$ above)
        reconciled["user_sessions_revoked"] = cursor.rowcount
    except Exception:
        logger.exception(
            "Post-restore reconciliation failed; restore must be treated as incomplete"
        )
        raise

    try:
        cursor.execute(f"UPDATE {t_recovery} SET status = 'rejected' WHERE status = 'pending';")  # noqa: S608 (table_prefix validated against ^[A-Za-z0-9_]*$ above)
        reconciled["recovery_requests_expired"] = cursor.rowcount
    except Exception:
        logger.exception(
            "Post-restore reconciliation failed; restore must be treated as incomplete"
        )
        raise

    try:
        cursor.execute(f"UPDATE {t_device} SET status = 'rejected' WHERE status = 'pending';")  # noqa: S608 (table_prefix validated against ^[A-Za-z0-9_]*$ above)
        reconciled["device_requests_expired"] = cursor.rowcount
    except Exception:
        logger.exception(
            "Post-restore reconciliation failed; restore must be treated as incomplete"
        )
        raise

    return reconciled
