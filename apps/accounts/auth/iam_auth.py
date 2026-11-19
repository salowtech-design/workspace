"""
Dedicated IAM Administrator Authentication, Session Authority, and Assurance Engine.
Implements Non-Negotiable Architecture (Section 3 & 4.1):
1. Dedicated IAM administrator identities with min 16-character password policy.
2. Independent IAM password verification (Argon2id) and independent RFC 6238 TOTP.
3. Server-side opaque IAM session authority (__Host-iam_session).
4. Centralized server-side Assurance Levels:
   - Level 1 (L1): IAM ID + Password + Fresh TOTP.
   - Level 2 (L2): Action-bound step-up re-authentication (5-minute TTL).
   - Level 3 (L3): Dual-control authorization with independent second approver (no self-approval).
5. Protection for the last active Super Admin.
"""

import secrets
import time
from datetime import timedelta
from typing import Optional, Tuple

import pyotp
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.crypto import decrypt_totp_secret, encrypt_totp_secret
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.lockout import check_lockout, clear_failed_attempts, record_failed_attempt
from apps.accounts.models import AuthEvent, CustomUser, IAMAdminIdentity, IAMSession

IAM_ADMIN_MIN_PASSWORD_LENGTH = 16
IAM_SESSION_COOKIE_NAME = getattr(settings, "IAM_SESSION_COOKIE_NAME", "__Host-iam_session")
IAM_SESSION_IDLE_TIMEOUT_MINUTES = 30
IAM_SESSION_ABSOLUTE_TIMEOUT_HOURS = 12


def create_iam_admin_identity(
    user: CustomUser,
    admin_id: str,
    password: str,
    role: str = IAMAdminIdentity.Role.ADMIN,
) -> Tuple[IAMAdminIdentity, str]:
    """
    Provisions a dedicated IAM administrator identity linked to an authoritative person/employee record.
    Enforces minimum 16-character password and dedicated cryptographic TOTP secret.
    """
    clean_admin_id = admin_id.strip().upper()
    if len(password) < IAM_ADMIN_MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"IAM administrator passwords must be at least {IAM_ADMIN_MIN_PASSWORD_LENGTH} characters."
        )

    if IAMAdminIdentity.objects.filter(admin_id=clean_admin_id).exists():
        raise ValidationError(f"IAM Admin ID '{clean_admin_id}' is already provisioned.")

    # Generate independent TOTP secret for IAM administration
    raw_totp_secret = pyotp.random_base32()
    enc_secret, nonce, key_id = encrypt_totp_secret(raw_totp_secret)

    with transaction.atomic():
        iam_admin = IAMAdminIdentity.objects.create(
            user=user,
            admin_id=clean_admin_id,
            password_hash=make_password(password),
            encrypted_totp_secret=enc_secret,
            totp_nonce=nonce,
            totp_key_id=key_id,
            totp_confirmed_at=timezone.now(),
            role=role,
            status=IAMAdminIdentity.Status.ACTIVE,
        )

        log_auth_event(
            event="iam_admin_provisioned",
            code="INFO",
            step="iam_provisioning",
            employee_id=user.employee_id,
            reference_id=clean_admin_id,
            message=f"Dedicated IAM Admin [{clean_admin_id}] provisioned with role {role}",
        )

    return iam_admin, raw_totp_secret


def authenticate_iam_admin(
    admin_id: str,
    password: str,
    totp_code: str,
    client_ip: Optional[str] = None,
) -> IAMAdminIdentity:
    """
    Executes dedicated IAM Administrator sign-in (Section 4.1):
    1. Dedicated IAM administrator ID.
    2. Dedicated IAM password (min 16 chars).
    3. Fresh IAM TOTP.
    4. Account lifecycle and risk checks.
    5. Returns authenticated IAMAdminIdentity.
    """
    clean_admin_id = admin_id.strip().upper()
    lockout_key = f"iam_login:{clean_admin_id}"
    check_lockout(lockout_key)

    iam_admin = (
        IAMAdminIdentity.objects.select_related("user")
        .filter(admin_id=clean_admin_id)
        .first()
    )

    if not iam_admin:
        record_failed_attempt(lockout_key, threshold=3)
        # Prevent timing enumeration
        check_password(password, "pbkdf2_sha256$600000$dummy$dummypasswordhashforunknownaccounts")
        raise AuthException(
            "AUTH-IAM-001",
            step="iam_login",
            message_override="Invalid IAM administrator credentials.",
        )

    if iam_admin.status != IAMAdminIdentity.Status.ACTIVE:
        raise AuthException(
            "AUTH-IAM-002",
            step="iam_login",
            message_override=f"IAM administrator account is {iam_admin.status}.",
        )

    # 1. Verify password
    if not check_password(password, iam_admin.password_hash):
        record_failed_attempt(lockout_key, employee_id=iam_admin.user.employee_id, threshold=3)
        iam_admin.failed_login_attempts += 1
        if iam_admin.failed_login_attempts >= 3:
            iam_admin.status = IAMAdminIdentity.Status.LOCKED
            iam_admin.locked_until = timezone.now() + timedelta(minutes=30)
        iam_admin.save(update_fields=["failed_login_attempts", "status", "locked_until"])
        raise AuthException(
            "AUTH-IAM-001",
            step="iam_login",
            message_override="Invalid IAM administrator credentials.",
        )

    # 2. Verify independent TOTP
    if not iam_admin.encrypted_totp_secret:
        raise AuthException(
            "AUTH-IAM-003",
            step="iam_login",
            message_override="IAM Administrator TOTP is not enrolled.",
        )

    raw_secret = decrypt_totp_secret(
        bytes(iam_admin.encrypted_totp_secret),
        bytes(iam_admin.totp_nonce) if iam_admin.totp_nonce else b"",
        iam_admin.totp_key_id,
    )
    totp = pyotp.TOTP(raw_secret, interval=30)
    current_step = int(time.time() // 30)

    # Anti-replay: cannot reuse same TOTP step
    if totp.verify(totp_code.strip(), valid_window=1):
        # Prevent step replay
        if current_step <= iam_admin.last_used_step:
            raise AuthException(
                "AUTH-IAM-004",
                step="iam_login",
                message_override="TOTP code has already been used. Please wait for the next 30-second token.",
            )
        iam_admin.last_used_step = current_step
    else:
        record_failed_attempt(lockout_key, employee_id=iam_admin.user.employee_id, threshold=3)
        raise AuthException(
            "AUTH-IAM-005",
            step="iam_login",
            message_override="Invalid IAM TOTP verification code.",
        )

    # Reset failure tracking on full success
    clear_failed_attempts(lockout_key)
    iam_admin.failed_login_attempts = 0
    iam_admin.last_login_at = timezone.now()
    iam_admin.save(update_fields=["failed_login_attempts", "last_login_at", "last_used_step"])

    log_auth_event(
        event="iam_admin_authenticated",
        code="INFO",
        step="iam_login",
        employee_id=iam_admin.user.employee_id,
        network_address=client_ip,
        reference_id=iam_admin.admin_id,
        message=f"Dedicated IAM Admin [{iam_admin.admin_id}] authenticated (Assurance Level 1)",
    )

    return iam_admin


def create_iam_session(
    iam_admin: IAMAdminIdentity,
    request,
    assurance_level: int = 1,
) -> IAMSession:
    """
    Creates an independent server-side session authority for iam.salowtech.com.
    """
    session_key = secrets.token_urlsafe(48)
    client_ip = getattr(request, "client_ip", None) or request.META.get("REMOTE_ADDR", "127.0.0.1")
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:255]

    session = IAMSession.objects.create(
        session_key=session_key,
        iam_admin=iam_admin,
        assurance_level=assurance_level,
        ip_address=client_ip,
        user_agent=user_agent,
        is_active=True,
    )

    request.session_key_iam = session_key
    request.iam_admin = iam_admin
    request.iam_session = session

    return session


def get_iam_session_from_request(request) -> Optional[IAMSession]:
    """
    Resolves the authoritative IAMSession from the dedicated IAM cookie (__Host-iam_session).
    Enforces idle expiry (30 min) and absolute expiry (12 hours).
    """
    cookie_name = IAM_SESSION_COOKIE_NAME
    session_key = request.COOKIES.get(cookie_name) or request.COOKIES.get("iam_session_id")

    if not session_key:
        return None

    session = (
        IAMSession.objects.select_related("iam_admin", "iam_admin__user")
        .filter(session_key=session_key, is_active=True)
        .first()
    )

    if not session:
        return None

    now = timezone.now()
    # 1. Idle expiry check (30 minutes)
    if session.last_activity and now - session.last_activity > timedelta(minutes=IAM_SESSION_IDLE_TIMEOUT_MINUTES):
        session.is_active = False
        session.save(update_fields=["is_active"])
        return None

    # 2. Absolute expiry check (12 hours)
    if session.created_at and now - session.created_at > timedelta(hours=IAM_SESSION_ABSOLUTE_TIMEOUT_HOURS):
        session.is_active = False
        session.save(update_fields=["is_active"])
        return None

    # 3. Account status check
    if session.iam_admin.status != IAMAdminIdentity.Status.ACTIVE:
        session.is_active = False
        session.save(update_fields=["is_active"])
        return None

    # Update last activity
    session.last_activity = now
    session.save(update_fields=["last_activity"])
    return session


def stepup_iam_assurance(
    iam_session: IAMSession,
    password: str,
    totp_code: str,
    action_name: str,
) -> bool:
    """
    Elevates IAM session to Assurance Level 2 (L2) for sensitive operations.
    Strictly bound to exact action_name and valid for 5 minutes.
    """
    iam_admin = iam_session.iam_admin
    if not check_password(password, iam_admin.password_hash):
        return False

    raw_secret = decrypt_totp_secret(
        bytes(iam_admin.encrypted_totp_secret),
        bytes(iam_admin.totp_nonce) if iam_admin.totp_nonce else b"",
        iam_admin.totp_key_id,
    )
    totp = pyotp.TOTP(raw_secret, interval=30)
    current_step = int(time.time() // 30)

    if not totp.verify(totp_code.strip(), valid_window=1):
        return False

    if current_step <= iam_admin.last_used_step:
        return False

    iam_admin.last_used_step = current_step
    iam_admin.save(update_fields=["last_used_step"])

    iam_session.assurance_level = 2
    iam_session.stepup_action = action_name
    iam_session.stepup_expires_at = timezone.now() + timedelta(minutes=5)
    iam_session.save(update_fields=["assurance_level", "stepup_action", "stepup_expires_at"])

    log_auth_event(
        event="iam_assurance_elevated",
        code="INFO",
        step="stepup",
        employee_id=iam_admin.user.employee_id,
        reference_id=iam_admin.admin_id,
        message=f"IAM session elevated to Level 2 for action '{action_name}' (expires in 5m)",
    )

    return True


def check_iam_assurance(
    request,
    required_level: int,
    action_name: str,
    approver_admin: Optional[IAMAdminIdentity] = None,
) -> bool:
    """
    Centralized server-side assurance enforcement (Section 9):
    - Level 1: Dedicated IAM Administrator session.
    - Level 2: Sensitive action step-up re-authentication bound to action and valid.
    - Level 3: Privileged operation requiring Level 2 AND a second independent approver (no self-approval).
    """
    iam_session: Optional[IAMSession] = getattr(request, "iam_session", None)
    if not iam_session or not iam_session.is_active:
        return False

    if required_level <= 1:
        return iam_session.assurance_level >= 1

    now = timezone.now()
    if required_level == 2:
        if (
            iam_session.assurance_level >= 2
            and iam_session.stepup_action == action_name
            and iam_session.stepup_expires_at
            and iam_session.stepup_expires_at > now
        ):
            return True
        return False

    if required_level >= 3:
        # Check Level 2 conditions first
        if not (
            iam_session.assurance_level >= 2
            and iam_session.stepup_action == action_name
            and iam_session.stepup_expires_at
            and iam_session.stepup_expires_at > now
        ):
            return False

        # Dual Control: Requires an independent authorized second approver
        if not approver_admin:
            return False

        # Strict No Self-Approval (Section 9)
        if approver_admin.id == iam_session.iam_admin.id:
            return False

        # Second approver must be an active Super Admin
        if (
            approver_admin.role != IAMAdminIdentity.Role.SUPER_ADMIN
            or approver_admin.status != IAMAdminIdentity.Status.ACTIVE
        ):
            return False

        return True

    return False


def protect_last_super_admin(target_admin: IAMAdminIdentity):
    """
    Guarantees the last active Super Admin can never be suspended, locked, or demoted.
    """
    if target_admin.role == IAMAdminIdentity.Role.SUPER_ADMIN:
        active_super_admins = (
            IAMAdminIdentity.objects.filter(
                role=IAMAdminIdentity.Role.SUPER_ADMIN,
                status=IAMAdminIdentity.Status.ACTIVE,
            )
            .exclude(id=target_admin.id)
            .count()
        )
        if active_super_admins == 0:
            raise ValidationError(
                "Operation prohibited: Cannot demote, suspend, or delete the last active Super Admin."
            )
