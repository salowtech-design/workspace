"""
Pure Python RFC 6238 TOTP (Time-Based One-Time Password) Implementation.
Provides zero-external-dependency, timing-safe multi-factor authentication.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def generate_totp_secret() -> str:
    """Generate a cryptographically secure 160-bit Base32 TOTP secret."""
    raw_bytes = secrets.token_bytes(20)
    return base64.b32encode(raw_bytes).decode("utf-8").replace("=", "")


def get_totp_uri(secret_base32: str, employee_id: str, issuer: str = "Salow Workspace") -> str:
    """Build otpauth:// URI for standard authenticator apps."""
    label = quote(f"{issuer}:{employee_id}")
    enc_issuer = quote(issuer)
    return f"otpauth://totp/{label}?secret={secret_base32}&issuer={enc_issuer}&algorithm=SHA1&digits=6&period=30"


def generate_totp_code(secret_base32: str, intervals_no: int = None) -> str:
    """Compute 6-digit TOTP code for a given 30-second interval."""
    if intervals_no is None:
        intervals_no = int(time.time()) // 30

    # Ensure padding for base32 decode
    padding = "=" * ((8 - len(secret_base32) % 8) % 8)
    key = base64.b32decode(secret_base32 + padding, casefold=True)
    msg = struct.pack(">Q", intervals_no)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[19] & 0x0F
    code_int = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code_int:06d}"


def verify_totp_token(secret_base32: str, token: str, window: int = 1) -> bool:
    """
    Verify 6-digit code against secret with timing-safe comparison.
    window=1 allows current interval ±30 seconds (tolerating slight clock drift).
    """
    cleaned_token = token.strip().replace(" ", "")
    if len(cleaned_token) != 6 or not cleaned_token.isdigit():
        return False

    current_interval = int(time.time()) // 30
    for offset in range(-window, window + 1):
        expected_code = generate_totp_code(secret_base32, current_interval + offset)
        if hmac.compare_digest(expected_code, cleaned_token):
            return True
    return False


def generate_recovery_codes(count: int = 8) -> list[str]:
    """Generate list of human-readable single-use recovery codes."""
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(5).upper()
        formatted = f"{raw[:5]}-{raw[5:]}"
        codes.append(formatted)
    return codes


def hash_recovery_code(code: str) -> str:
    """Hash recovery code with SHA-256 for secure storage."""
    normalized = code.strip().upper().replace("-", "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
