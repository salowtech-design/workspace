"""
W3C WebAuthn / FIDO2 Passkey Service.
Follows W3C WebAuthn Level 3 specifications.
Supports passkey registration, multi-factor assertion, credential revocation,
and device metadata tracking.
"""

from typing import Optional

import webauthn
from django.conf import settings
from django.utils import timezone
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.errors import AuthException
from apps.accounts.models import CustomUser, PasskeyCredential

REG_CHALLENGE_SESSION_KEY = "_webauthn_reg_challenge"
AUTH_CHALLENGE_SESSION_KEY = "_webauthn_auth_challenge"


def get_rp_id(request) -> str:
    """Resolve Relying Party identifier (domain) from request or settings."""
    configured = getattr(settings, "WEBAUTHN_RP_ID", None)
    if configured:
        return configured
    host = request.get_host().split(":")[0]
    return host if host else "localhost"


def get_origin(request) -> str:
    """Resolve expected web origin from request or settings."""
    configured = getattr(settings, "WEBAUTHN_ORIGIN", None)
    if configured:
        return configured
    scheme = "https" if request.is_secure() else "http"
    return f"{scheme}://{request.get_host()}"


def generate_passkey_registration_options(request, user: CustomUser) -> str:
    """
    Generate cryptographic challenge and W3C registration options for passkey enrollment.
    Challenge is safely bound to the authenticated user's session.
    """
    rp_id = get_rp_id(request)
    existing_credentials = [
        PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(cred.credential_id))
        for cred in user.passkeys.filter(is_active=True)
    ]

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Salow Workspace",
        user_id=str(user.id).encode("utf-8"),
        user_name=user.employee_id,
        user_display_name=user.full_name,
        exclude_credentials=existing_credentials,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
            COSEAlgorithmIdentifier.EDDSA,
        ],
    )

    request.session[REG_CHALLENGE_SESSION_KEY] = options.challenge.hex()
    return options_to_json(options)


def verify_passkey_registration(
    request,
    user: CustomUser,
    credential_data: dict,
    display_name: str = "My Passkey",
) -> PasskeyCredential:
    """
    Verify browser WebAuthn attestation response and persist credential public key.
    """
    challenge_hex = request.session.pop(REG_CHALLENGE_SESSION_KEY, None)
    if not challenge_hex:
        raise AuthException("AUTH-MFA-001", step="passkey_reg", employee_id=user.employee_id)

    expected_challenge = bytes.fromhex(challenge_hex)
    rp_id = get_rp_id(request)
    origin = get_origin(request)

    try:
        verification = verify_registration_response(
            credential=credential_data,
            expected_challenge=expected_challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception as e:
        log_auth_event(
            event="passkey_reg_failed",
            code="AUTH-MFA-002",
            step="passkey_reg",
            employee_id=user.employee_id,
            message=f"Passkey registration signature check failed: {e}",
        )
        raise AuthException("AUTH-MFA-002", step="passkey_reg", employee_id=user.employee_id) from e

    credential_id_str = credential_data.get("id") or webauthn.helpers.bytes_to_base64url(
        verification.credential_id
    )

    passkey = PasskeyCredential.objects.create(
        user=user,
        credential_id=credential_id_str,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        aaguid=str(verification.aaguid),
        display_name=display_name.strip() or "Passkey",
    )

    log_auth_event(
        event="passkey_registered",
        code="INFO",
        step="passkey_reg",
        employee_id=user.employee_id,
        reference_id=f"PK-{passkey.id}",
        message=f"Passkey '{passkey.display_name}' registered successfully",
    )

    return passkey


def generate_passkey_authentication_options(
    request,
    user: Optional[CustomUser] = None,
) -> str:
    """
    Generate W3C assertion request options for passkey sign-in.
    """
    rp_id = get_rp_id(request)
    allow_credentials = None
    if user:
        allow_credentials = [
            PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(cred.credential_id))
            for cred in user.passkeys.filter(is_active=True)
        ]

    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.REQUIRED,
    )

    request.session[AUTH_CHALLENGE_SESSION_KEY] = options.challenge.hex()
    return options_to_json(options)


def verify_passkey_authentication(
    request,
    credential_data: dict,
    user: Optional[CustomUser] = None,
) -> CustomUser:
    """
    Verify W3C assertion signature from user device and authenticate account.
    """
    challenge_hex = request.session.pop(AUTH_CHALLENGE_SESSION_KEY, None)
    if not challenge_hex:
        raise AuthException("AUTH-MFA-001", step="passkey_auth")

    expected_challenge = bytes.fromhex(challenge_hex)
    rp_id = get_rp_id(request)
    origin = get_origin(request)

    credential_id = credential_data.get("id")
    if not credential_id:
        raise AuthException("AUTH-MFA-002", step="passkey_auth")

    # Locate credential
    passkey = (
        PasskeyCredential.objects.filter(credential_id=credential_id, is_active=True)
        .select_related("user")
        .first()
    )
    if not passkey:
        raise AuthException("AUTH-MFA-001", step="passkey_auth")

    matched_user = passkey.user
    if user and user.id != matched_user.id:
        raise AuthException("AUTH-MFA-002", step="passkey_auth", employee_id=user.employee_id)

    try:
        verification = verify_authentication_response(
            credential=credential_data,
            expected_challenge=expected_challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception as e:
        log_auth_event(
            event="passkey_auth_failed",
            code="AUTH-MFA-002",
            step="passkey_auth",
            employee_id=matched_user.employee_id,
            message=f"WebAuthn assertion verification failed: {e}",
        )
        raise AuthException(
            "AUTH-MFA-002", step="passkey_auth", employee_id=matched_user.employee_id
        ) from e

    # Update sign counter and last used timestamp
    passkey.sign_count = verification.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=["sign_count", "last_used_at"])

    log_auth_event(
        event="passkey_authenticated",
        code="INFO",
        step="passkey_auth",
        employee_id=matched_user.employee_id,
        reference_id=f"PK-{passkey.id}",
        message=f"Passkey '{passkey.display_name}' authenticated successfully",
    )

    return matched_user


def revoke_passkey(user: CustomUser, passkey_id: int) -> bool:
    """Revoke/deactivate a user passkey credential."""
    updated = PasskeyCredential.objects.filter(id=passkey_id, user=user).update(is_active=False)
    if updated:
        log_auth_event(
            event="passkey_revoked",
            code="INFO",
            step="passkey_revoke",
            employee_id=user.employee_id,
            reference_id=f"PK-{passkey_id}",
            message="Passkey revoked by user",
        )
        return True
    return False
