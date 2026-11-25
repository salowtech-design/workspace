"""
IAM administrator setup and restricted recovery.

Flow
1. An operator invites an existing Workspace person as a PENDING IAM admin. A single-use link
   (10 minutes) is printed once. Only its keyed hash is stored.
2. The person opens the link, sets the IAM password (16+ characters), scans the QR code and
   confirms the first authenticator code. Only then the account becomes ACTIVE.
3. Ten single-use recovery codes are shown once (keyed hashes stored).
4. A recovery code plus the IAM password does not open an admin session. It revokes all IAM sessions,
   puts the account back to PENDING and issues a new setup link, so the person must enroll a new
   authenticator (and receives a new code set).

A pending account can never sign in (authenticate_iam_admin accepts ACTIVE only).
"""

import hmac
import secrets
from datetime import timedelta
from typing import Optional

import pyotp
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import get_activation_hmac_key
from apps.accounts.auth.crypto import decrypt_totp_secret, encrypt_totp_secret
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.lockout import check_lockout, clear_failed_attempts, record_failed_attempt
from apps.accounts.auth.mfa_totp import get_otpauth_uri, render_svg_qr
from apps.accounts.auth.security_ops import create_security_alert
from apps.accounts.models import (
    CustomUser,
    IAMAdminIdentity,
    IAMDualApprovalRequest,
    IAMRecoveryCode,
    IAMSession,
    IAMSetupToken,
    PrivilegeElevation,
    SecurityAlert,
)

SETUP_TOKEN_MINUTES = 10
SETUP_MAX_BAD_CODES = 5
IAM_PASSWORD_MIN = 16
RECOVERY_CODE_COUNT = 10


class SetupError(Exception):
    """User-safe message; never carries secrets."""


def _hash(raw: str) -> str:
    normalized = raw.strip().replace("-", "").replace(" ", "").upper()
    return hmac.new(
        get_activation_hmac_key(), b"iam-recovery-code:" + normalized.encode("utf-8"), "sha256"
    ).hexdigest()


def _hash_token(raw: str) -> str:
    return hmac.new(
        get_activation_hmac_key(), b"iam-setup-token:" + raw.strip().encode("utf-8"), "sha256"
    ).hexdigest()


def new_recovery_codes() -> list[str]:
    out = []
    for _ in range(RECOVERY_CODE_COUNT):
        h = secrets.token_hex(5).upper()
        out.append(f"{h[:5]}-{h[5:]}")
    return out


def revoke_iam_sessions(admin: IAMAdminIdentity) -> dict:
    """Close every IAM session of this admin and end the linked person's elevated access.

    Returns counts for the audit row. Errors are not swallowed: the caller's transaction rolls back,
    so a recovery never reports success while privileges remain."""
    from apps.accounts.auth.stepup import mark_sudo_revoked

    sessions = IAMSession.objects.filter(iam_admin=admin, is_active=True).update(is_active=False)
    elevations = PrivilegeElevation.objects.filter(
        user_id_ref=admin.user_id, kind__in=["sudo", "break_glass"]
    ).delete()[0]
    requests = IAMDualApprovalRequest.objects.filter(
        Q(requester=admin) | Q(approver=admin),
        status__in=[IAMDualApprovalRequest.Status.PENDING, IAMDualApprovalRequest.Status.APPROVED],
    ).update(status=IAMDualApprovalRequest.Status.REJECTED)
    # Persistent epoch: a sudo marker kept in a workspace session cookie older than now is void.
    mark_sudo_revoked(admin.user_id)
    return {"sessions": sessions, "elevations": elevations, "l3_requests": requests}


def _clear_pending_secret(admin: IAMAdminIdentity) -> None:
    """An unconfirmed authenticator secret never survives a new link or a burned link."""
    if admin.totp_confirmed_at is None:
        IAMAdminIdentity.objects.filter(pk=admin.pk).update(
            encrypted_totp_secret=None, totp_nonce=None, totp_key_id="", last_used_step=0
        )
        admin.encrypted_totp_secret = None
        admin.totp_nonce = None
        admin.totp_key_id = ""
        admin.last_used_step = 0


def _issue_token(admin: IAMAdminIdentity, purpose: str) -> str:
    IAMSetupToken.objects.filter(iam_admin=admin, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    _clear_pending_secret(admin)
    raw = secrets.token_urlsafe(32)
    IAMSetupToken.objects.create(
        iam_admin=admin,
        token_hash=_hash_token(raw),
        purpose=purpose,
        expires_at=timezone.now() + timedelta(minutes=SETUP_TOKEN_MINUTES),
    )
    return raw


def invite_iam_admin(
    employee_id: str, admin_id: str, role: str = IAMAdminIdentity.Role.ADMIN
) -> tuple[IAMAdminIdentity, str]:
    """Operator action: make an existing active Workspace person a PENDING IAM admin."""
    if role not in IAMAdminIdentity.Role.values:
        raise ValidationError(f"Unknown IAM role '{role}'.")
    user = CustomUser.objects.filter(employee_id=employee_id.strip().upper()).first()
    if user is None:
        raise ValidationError("No Workspace person with that employee ID.")
    if user.status != CustomUser.Status.ACTIVE or not user.is_active:
        raise ValidationError("The Workspace person must be active.")
    clean_id = admin_id.strip().upper()
    with transaction.atomic():
        existing = IAMAdminIdentity.objects.select_for_update().filter(user=user).first()
        if existing is not None:
            if existing.status != IAMAdminIdentity.Status.PENDING:
                raise ValidationError("That person already has an IAM administrator account.")
            admin = existing
        else:
            if IAMAdminIdentity.objects.filter(admin_id=clean_id).exists():
                raise ValidationError(f"IAM Admin ID '{clean_id}' is already in use.")
            admin = IAMAdminIdentity.objects.create(
                user=user,
                admin_id=clean_id,
                password_hash=make_password(None),
                role=role,
                status=IAMAdminIdentity.Status.PENDING,
            )
        had_factor = IAMRecoveryCode.objects.filter(iam_admin=admin).exists()
        raw = _issue_token(
            admin,
            IAMSetupToken.Purpose.REPLACE_FACTOR if had_factor else IAMSetupToken.Purpose.ENROLL,
        )
        log_auth_event(
            event="iam_admin_invited",
            code="INFO",
            step="iam_setup",
            employee_id=user.employee_id,
            reference_id=admin.admin_id,
            message=f"IAM admin [{admin.admin_id}] invited with role {role}; setup link issued (single use, {SETUP_TOKEN_MINUTES} min)",
        )
        create_security_alert(
            category=SecurityAlert.Category.PRIVILEGED_ROLE_CHANGE,
            severity=SecurityAlert.Severity.HIGH,
            title=f"IAM admin invited: {admin.admin_id} ({role})",
            description="Pending until the person sets a password and confirms an authenticator.",
            employee_id=user.employee_id,
            reference_id=admin.admin_id,
        )
    return admin, raw


def _live_token(raw: str) -> IAMSetupToken:
    tok = (
        IAMSetupToken.objects.select_for_update()
        .select_related("iam_admin", "iam_admin__user")
        .filter(token_hash=_hash_token(raw or ""))
        .first()
    )
    if (
        tok is None
        or tok.used_at is not None
        or tok.expires_at <= timezone.now()
        or tok.iam_admin.status != IAMAdminIdentity.Status.PENDING
        or tok.iam_admin.user.status != CustomUser.Status.ACTIVE
        or not tok.iam_admin.user.is_active
    ):
        raise SetupError("This setup link is not valid. Ask for a new one.")
    return tok


def exchange_link(raw: str) -> str:
    """Swap the token carried in a URL for a fresh one that never appears in a URL.
    The URL token stops working at once, so a copy in logs, history or a referrer is dead."""
    with transaction.atomic():
        tok = _live_token(raw)
        fresh = secrets.token_urlsafe(32)
        tok.token_hash = _hash_token(fresh)
        tok.save(update_fields=["token_hash"])
        return fresh


def setup_state(raw: str) -> dict:
    """Which step the link is on: 'password' or 'authenticator'."""
    with transaction.atomic():
        tok = _live_token(raw)
        needs_password = (
            tok.purpose == IAMSetupToken.Purpose.ENROLL
            and not tok.iam_admin.has_usable_password_hash()
        )
        return {"step": "password" if needs_password else "authenticator", "admin": tok.iam_admin}


def _pending_secret(admin: IAMAdminIdentity) -> str:
    if admin.encrypted_totp_secret and admin.totp_confirmed_at is None:
        return decrypt_totp_secret(
            bytes(admin.encrypted_totp_secret), bytes(admin.totp_nonce or b""), admin.totp_key_id
        )
    secret = pyotp.random_base32()
    enc, nonce, key_id = encrypt_totp_secret(secret)
    admin.encrypted_totp_secret, admin.totp_nonce, admin.totp_key_id = enc, nonce, key_id
    admin.totp_confirmed_at = None
    admin.last_used_step = 0
    admin.save(
        update_fields=[
            "encrypted_totp_secret",
            "totp_nonce",
            "totp_key_id",
            "totp_confirmed_at",
            "last_used_step",
        ]
    )
    return secret


def set_password(raw: str, password: str, confirm: str) -> None:
    if len(password) < IAM_PASSWORD_MIN:
        raise SetupError(f"The IAM password must be at least {IAM_PASSWORD_MIN} characters.")
    if password != confirm:
        raise SetupError("The two passwords do not match.")
    with transaction.atomic():
        tok = _live_token(raw)
        admin = tok.iam_admin
        if tok.purpose != IAMSetupToken.Purpose.ENROLL or admin.has_usable_password_hash():
            raise SetupError("The password is already set for this link.")
        admin.password_hash = make_password(password)
        admin.save(update_fields=["password_hash"])
        log_auth_event(
            event="iam_admin_password_set",
            code="INFO",
            step="iam_setup",
            employee_id=admin.user.employee_id,
            actor_employee_id=admin.user.employee_id,
            reference_id=admin.admin_id,
            message=f"IAM admin [{admin.admin_id}] set the IAM password through the setup link",
        )


def qr_for(raw: str) -> dict:
    """Authenticator QR for a link that is past the password step (secret is created once)."""
    with transaction.atomic():
        tok = _live_token(raw)
        admin = IAMAdminIdentity.objects.select_for_update().get(pk=tok.iam_admin_id)
        if tok.purpose == IAMSetupToken.Purpose.ENROLL and not admin.has_usable_password_hash():
            raise SetupError("Set the IAM password first.")
        secret = _pending_secret(admin)
        uri = get_otpauth_uri(secret, admin.admin_id)
        return {"svg": render_svg_qr(uri), "secret": secret, "admin_id": admin.admin_id}


def confirm_authenticator(raw: str, code: str, client_ip: Optional[str] = None) -> list[str]:
    codes, err = _confirm(raw, code, client_ip)
    if err:
        raise SetupError(err)
    return codes


def _confirm(raw, code, client_ip):
    """Failure bookkeeping must commit, so errors are returned and raised by the caller. Verify the first code. On success: ACTIVE, new recovery codes (returned once), sessions revoked."""
    code = (code or "").strip()
    with transaction.atomic():
        tok = _live_token(raw)
        admin = (
            IAMAdminIdentity.objects.select_for_update()
            .select_related("user")
            .get(pk=tok.iam_admin_id)
        )
        if not admin.encrypted_totp_secret or admin.totp_confirmed_at is not None:
            return None, "Scan the QR code first."
        secret = decrypt_totp_secret(
            bytes(admin.encrypted_totp_secret), bytes(admin.totp_nonce or b""), admin.totp_key_id
        )
        now = timezone.now()
        totp = pyotp.TOTP(secret, interval=30)
        step = None
        if code.isdigit() and len(code) == 6:
            base = int(now.timestamp() // 30)
            for off in (0, -1, 1):
                if totp.generate_otp(base + off) == code:
                    step = base + off
                    break
        if step is None:
            tok.failed_attempts += 1
            burned = tok.failed_attempts >= SETUP_MAX_BAD_CODES
            if burned:
                tok.used_at = now
                _clear_pending_secret(admin)
            tok.save(update_fields=["failed_attempts", "used_at"])
            log_auth_event(
                event="iam_admin_setup_code_failed",
                code="AUTH-IAM-005",
                step="iam_setup",
                employee_id=admin.user.employee_id,
                network_address=client_ip,
                reference_id=admin.admin_id,
                message=f"Wrong authenticator code during IAM setup for [{admin.admin_id}]"
                + ("; setup link burned after too many attempts" if burned else ""),
            )
            if burned:
                create_security_alert(
                    category=SecurityAlert.Category.REPEATED_AUTH_FAILURES,
                    severity=SecurityAlert.Severity.HIGH,
                    title=f"IAM setup link burned for {admin.admin_id}",
                    description="Too many wrong authenticator codes. A new invitation is needed.",
                    employee_id=admin.user.employee_id,
                    network_address=client_ip,
                    reference_id=admin.admin_id,
                )
                return None, "Too many wrong codes. This link is closed. Ask for a new one."
            return None, "That code is not correct. Try the current code from the app."

        replacing = tok.purpose == IAMSetupToken.Purpose.REPLACE_FACTOR
        admin.totp_confirmed_at = now
        admin.last_used_step = step
        admin.status = IAMAdminIdentity.Status.ACTIVE
        admin.failed_login_attempts = 0
        admin.locked_until = None
        admin.save(
            update_fields=[
                "totp_confirmed_at",
                "last_used_step",
                "status",
                "failed_login_attempts",
                "locked_until",
            ]
        )
        tok.used_at = now
        tok.save(update_fields=["used_at"])
        clear_failed_attempts(f"iam_login:{admin.admin_id}")
        revoked = revoke_iam_sessions(admin)
        codes = new_recovery_codes()
        IAMRecoveryCode.objects.filter(iam_admin=admin).delete()
        IAMRecoveryCode.objects.bulk_create(
            [IAMRecoveryCode(iam_admin=admin, keyed_hash=_hash(c)) for c in codes]
        )
        event = "iam_admin_authenticator_replaced" if replacing else "iam_admin_setup_completed"
        log_auth_event(
            event=event,
            code="INFO",
            step="iam_setup",
            employee_id=admin.user.employee_id,
            actor_employee_id=admin.user.employee_id,
            network_address=client_ip,
            reference_id=admin.admin_id,
            message=(
                f"IAM admin [{admin.admin_id}] "
                + ("replaced the authenticator" if replacing else "completed setup")
                + f"; {RECOVERY_CODE_COUNT} recovery codes issued; revoked: {revoked}"
            ),
        )
        create_security_alert(
            category=SecurityAlert.Category.MFA_RESET,
            severity=SecurityAlert.Severity.HIGH,
            title=(
                f"IAM authenticator replaced for {admin.admin_id}"
                if replacing
                else f"IAM admin {admin.admin_id} completed setup"
            ),
            employee_id=admin.user.employee_id,
            network_address=client_ip,
            reference_id=admin.admin_id,
        )
    return codes, None


def recover_with_code(
    admin_id: str, password: str, recovery_code: str, client_ip: Optional[str] = None
) -> str:
    """Recovery code + IAM password. Opens NO session: revokes sessions and returns a replace-factor link token."""
    clean_id = (admin_id or "").strip().upper()
    key = f"iam_login:{clean_id}"
    generic = AuthException(
        "AUTH-IAM-001",
        step="iam_recovery",
        message_override="Invalid IAM administrator credentials or recovery code.",
    )
    check_lockout(key)
    raw, failed = _recover(key, clean_id, password, recovery_code, client_ip)
    if failed:
        raise generic
    return raw


def _recover(key, clean_id, password, recovery_code, client_ip):
    with transaction.atomic():
        admin = (
            IAMAdminIdentity.objects.select_for_update()
            .select_related("user")
            .filter(admin_id=clean_id)
            .first()
        )
        if admin is None:
            record_failed_attempt(key, threshold=3)
            check_password(
                password, "pbkdf2_sha256$600000$dummy$dummypasswordhashforunknownaccounts"
            )
            return None, True
        if (
            admin.status != IAMAdminIdentity.Status.ACTIVE
            or admin.totp_confirmed_at is None
            or admin.user.status != CustomUser.Status.ACTIVE
            or not admin.user.is_active
        ):
            check_password(
                password, "pbkdf2_sha256$600000$dummy$dummypasswordhashforunknownaccounts"
            )
            return None, True
        if not check_password(password, admin.password_hash):
            record_failed_attempt(key, employee_id=admin.user.employee_id, threshold=3)
            return None, True
        code_hash = _hash(recovery_code or "")
        used = IAMRecoveryCode.objects.filter(
            iam_admin=admin, keyed_hash=code_hash, used_at__isnull=True
        ).update(used_at=timezone.now())
        if used != 1:
            record_failed_attempt(key, employee_id=admin.user.employee_id, threshold=3)
            return None, True
        clear_failed_attempts(key)
        admin.status = IAMAdminIdentity.Status.PENDING
        admin.encrypted_totp_secret = None
        admin.totp_nonce = None
        admin.totp_key_id = ""
        admin.totp_confirmed_at = None
        admin.save(
            update_fields=[
                "status",
                "encrypted_totp_secret",
                "totp_nonce",
                "totp_key_id",
                "totp_confirmed_at",
            ]
        )
        revoked = revoke_iam_sessions(admin)
        raw = _issue_token(admin, IAMSetupToken.Purpose.REPLACE_FACTOR)
        left = IAMRecoveryCode.objects.filter(iam_admin=admin, used_at__isnull=True).count()
        log_auth_event(
            event="iam_admin_recovery_code_redeemed",
            code="INFO",
            step="iam_recovery",
            employee_id=admin.user.employee_id,
            network_address=client_ip,
            reference_id=admin.admin_id,
            message=(
                f"Recovery code used for IAM admin [{admin.admin_id}]; account is pending until a new "
                f"authenticator is confirmed; revoked: {revoked}; {left} code(s) left"
            ),
        )
        create_security_alert(
            category=SecurityAlert.Category.RECOVERY_CODE_USED,
            severity=SecurityAlert.Severity.HIGH,
            title=f"IAM recovery code used for {admin.admin_id}",
            description="Sessions revoked. The account cannot sign in until a new authenticator is confirmed.",
            employee_id=admin.user.employee_id,
            network_address=client_ip,
            reference_id=admin.admin_id,
        )
    return raw, False
