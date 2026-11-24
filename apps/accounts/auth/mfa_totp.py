"""
RFC 6238 TOTP Multi-Factor Authentication Engine.
Implements AES-256-GCM secret encryption at rest, SVG QR code generation,
replay protection, and persistent pending secret caching.
"""

import io
import os
import time
from typing import Optional
from urllib.parse import quote

import pyotp
import qrcode
import qrcode.image.svg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import get_biometric_key_ring_dict
from apps.accounts.auth.errors import AuthException
from apps.accounts.models import CustomUser, TOTPCredential
from apps.face.engine.crypto import BiometricKeyRing


def get_auth_key_ring() -> BiometricKeyRing:
    """Retrieve key ring for TOTP secret encryption at rest."""
    keys = get_biometric_key_ring_dict()
    return BiometricKeyRing(keys=keys, active_key_id="k1")


def encrypt_secret(secret_base32: str, key_ring: BiometricKeyRing) -> tuple[bytes, bytes, str]:
    """Encrypt TOTP secret using AES-256-GCM with a fresh 96-bit nonce."""
    nonce = os.urandom(12)
    aesgcm = AESGCM(key_ring.active_key)
    key_id = key_ring.active_key_id
    ciphertext = aesgcm.encrypt(
        nonce, secret_base32.encode("utf-8"), associated_data=key_id.encode("utf-8")
    )
    return ciphertext, nonce, key_id


def decrypt_secret(
    encrypted_secret: bytes, nonce: bytes, key_id: str, key_ring: BiometricKeyRing
) -> str:
    """Decrypt AES-256-GCM encrypted TOTP secret."""
    key = key_ring.get_key(key_id)
    aesgcm = AESGCM(key)
    try:
        raw = aesgcm.decrypt(nonce, encrypted_secret, associated_data=key_id.encode("utf-8"))
        return raw.decode("utf-8")
    except InvalidTag as e:
        raise AuthException(
            "AUTH-MFA-001",
            step="decrypt_secret",
            message_override=(
                "Failed to decrypt TOTP secret. Key ring mismatch or corrupted record."
            ),
        ) from e


def render_svg_qr(otpauth_uri: str) -> str:
    """Render pure SVG QR code string without relying on Pillow/C image libraries."""
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
            image_factory=qrcode.image.svg.SvgPathImage,
        )
        qr.add_data(otpauth_uri)
        qr.make(fit=True)
        img = qr.make_image()

        stream = io.BytesIO()
        img.save(stream)
        svg_xml = stream.getvalue().decode("utf-8")
        # Strip XML declaration if present to allow inline embedding in HTML
        if "<?xml" in svg_xml:
            svg_xml = svg_xml[svg_xml.find("<svg") :]
        return svg_xml
    except Exception as exc:  # noqa: BLE001
        raise AuthException("AUTH-MFA-005", step="render_qr", exception=exc) from exc


# Canonical alias for QR generation
generate_qr_svg = render_svg_qr


def get_otpauth_uri(secret_base32: str, employee_id: str) -> str:
    """Construct standard otpauth URI with label Salowtech:<employee_id>."""
    issuer = "Salowtech"
    label = quote(f"{issuer}:{employee_id}")
    enc_issuer = quote(issuer)
    return f"otpauth://totp/{label}?secret={secret_base32}&issuer={enc_issuer}&algorithm=SHA1&digits=6&period=30"


def get_or_create_pending_totp(
    user: CustomUser, key_ring: Optional[BiometricKeyRing] = None
) -> tuple[str, str, str]:
    """
    Retrieve or initialize unconfirmed TOTP setup.
    If unconfirmed record already exists, reuse it so reloads don't break verification.
    Returns (secret_base32, otpauth_uri, svg_qr).
    """
    kr = key_ring or get_auth_key_ring()

    existing = TOTPCredential.objects.filter(user=user).first()
    if existing and existing.confirmed_at is None:
        secret = decrypt_secret(existing.encrypted_secret, existing.nonce, existing.key_id, kr)
        uri = get_otpauth_uri(secret, user.employee_id)
        svg = render_svg_qr(uri)
        return secret, uri, svg

    # Generate fresh 160-bit Base32 secret
    new_secret = pyotp.random_base32(length=32)
    ciphertext, nonce, key_id = encrypt_secret(new_secret, kr)

    with transaction.atomic():
        TOTPCredential.objects.update_or_create(
            user=user,
            defaults={
                "encrypted_secret": ciphertext,
                "nonce": nonce,
                "key_id": key_id,
                "confirmed_at": None,
                "last_used_step": 0,
            },
        )

    uri = get_otpauth_uri(new_secret, user.employee_id)
    svg = render_svg_qr(uri)
    return new_secret, uri, svg


TOTP_VERIFY_WINDOW = 1


def verify_totp_code(
    user: CustomUser,
    code_str: str,
    key_ring: Optional[BiometricKeyRing] = None,
    allow_unconfirmed: bool = False,
) -> bool:
    """
    Verify 6-digit code string against user's encrypted secret.
    Enforces replay protection and logs audit events.
    Accommodates clock skew between mobile devices and computer (window=1).
    """
    kr = key_ring or get_auth_key_ring()
    cleaned_code = code_str.strip().replace(" ", "")

    current_time = time.time()
    current_step = int(current_time) // 30

    # Verification window: ±1 step (±30 s), the standard. Wider windows multiply the guess space.
    window = TOTP_VERIFY_WINDOW

    # Atomic pessimistic locking across verification and state update
    with transaction.atomic():
        locked_cred = TOTPCredential.objects.select_for_update().filter(user=user).first()
        if not locked_cred:
            raise AuthException("AUTH-MFA-001", step="verify_totp", employee_id=user.employee_id)

        if not allow_unconfirmed and locked_cred.confirmed_at is None:
            raise AuthException("AUTH-MFA-006", step="verify_totp", employee_id=user.employee_id)

        secret = decrypt_secret(
            locked_cred.encrypted_secret, locked_cred.nonce, locked_cred.key_id, kr
        )
        totp = pyotp.TOTP(secret, interval=30, digits=6)

        is_valid = totp.verify(cleaned_code, for_time=current_time, valid_window=window)
        if not is_valid:
            log_auth_event(
                event="totp_invalid",
                code="AUTH-MFA-002",
                step="verify_totp",
                employee_id=user.employee_id,
                message="Submitted TOTP code does not match",
            )
            raise AuthException("AUTH-MFA-002", step="verify_totp", employee_id=user.employee_id)

        # Replay protection: identify which step matched
        matched_step = None
        offsets = [0]
        for i in range(1, window + 1):
            offsets.extend([-i, i])

        for step_offset in offsets:
            test_step = current_step + step_offset
            if totp.generate_otp(test_step) == cleaned_code:
                matched_step = test_step
                break

        step_to_record = matched_step or current_step
        if matched_step is not None and matched_step <= locked_cred.last_used_step:
            log_auth_event(
                event="totp_replay_rejected",
                code="AUTH-MFA-003",
                step="verify_totp",
                employee_id=user.employee_id,
                message=f"Replay detected: step {matched_step} <= {locked_cred.last_used_step}",
            )
            raise AuthException("AUTH-MFA-003", step="verify_totp", employee_id=user.employee_id)

        # Atomic conditional update across concurrent workers on all database engines
        now_ts = timezone.now()
        rows_updated = TOTPCredential.objects.filter(
            id=locked_cred.id,
            last_used_step__lt=step_to_record,
        ).update(
            last_used_step=step_to_record,
            confirmed_at=locked_cred.confirmed_at or now_ts,
        )
        if rows_updated == 0:
            log_auth_event(
                event="totp_replay_rejected",
                code="AUTH-MFA-003",
                step="verify_totp",
                employee_id=user.employee_id,
                message=f"Replay detected: step {step_to_record} already recorded by concurrent transaction",
            )
            raise AuthException("AUTH-MFA-003", step="verify_totp", employee_id=user.employee_id)

        locked_cred.last_used_step = step_to_record
        if locked_cred.confirmed_at is None:
            locked_cred.confirmed_at = now_ts

        log_auth_event(
            event="totp_verified",
            code="INFO",
            step="verify_totp",
            employee_id=user.employee_id,
            message="TOTP authentication successful",
        )

    return True
