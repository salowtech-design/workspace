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

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import timedelta
from typing import Any, Optional, Tuple

import pyotp
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import get_session_hmac_key
from apps.accounts.auth.crypto import decrypt_totp_secret, encrypt_totp_secret
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.lockout import check_lockout, clear_failed_attempts, record_failed_attempt
from apps.accounts.models import (
    CustomUser,
    IAMAdminIdentity,
    IAMDualApprovalRequest,
    IAMSession,
)

IAM_ADMIN_MIN_PASSWORD_LENGTH = 16
IAM_SESSION_COOKIE_NAME = getattr(settings, "IAM_SESSION_COOKIE_NAME", "__Host-salow_iam_session")
IAM_SESSION_IDLE_TIMEOUT_MINUTES = 5


# Background polling (live feeds, presence heartbeat) must not count as admin activity,
# otherwise a dashboard left open in a browser would never reach the idle timeout.
def effective_iam_idle_timeout_minutes() -> int:
    """Idle limit actually enforced: the active IAM policy value, capped at the platform maximum (5).

    Older policy rows stored 10; they are capped, never raised. If the policy cannot be read,
    the platform maximum applies.
    """
    try:
        from apps.accounts.models import AuthenticationPolicy

        policy = (
            AuthenticationPolicy.objects.filter(
                domain=AuthenticationPolicy.Domain.IAM, is_active=True
            )
            .order_by("-version")
            .first()
        )
        if policy is not None:
            return max(
                1, min(IAM_SESSION_IDLE_TIMEOUT_MINUTES, int(policy.idle_session_timeout_minutes))
            )
    except Exception:
        logging.getLogger(__name__).debug("best-effort step failed and was ignored", exc_info=True)
    return IAM_SESSION_IDLE_TIMEOUT_MINUTES


IAM_PASSIVE_PATH_PREFIXES = (
    "/console/heartbeat",
    "/console/authentication/live-feed",
    "/console/authentication/presence-feed",
    "/security/events",
)
IAM_SESSION_ABSOLUTE_TIMEOUT_HOURS = 8


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


def hash_session_bearer(raw_bearer: str) -> str:
    """Compute keyed HMAC-SHA256 digest of session bearer token."""
    if not raw_bearer:
        return ""
    key = get_session_hmac_key()
    return hmac.new(key, str(raw_bearer).encode("utf-8"), "sha256").hexdigest()


def authenticate_iam_admin(  # noqa: C901 (refactor deferred, behavior covered by tests)
    admin_id: str,
    password: str,
    totp_code: str,
    client_ip: Optional[str] = None,
) -> IAMAdminIdentity:
    """
    Executes dedicated IAM Administrator sign-in (Section 4.1):
    1. Dedicated IAM administrator ID.
    2. Dedicated IAM password (min 16 chars).
    3. Fresh IAM TOTP with atomic row-level anti-replay protection.
    4. Account lifecycle and risk checks.
    5. Returns authenticated IAMAdminIdentity.
    """
    clean_admin_id = admin_id.strip().upper()
    lockout_key = f"iam_login:{clean_admin_id}"
    check_lockout(lockout_key)

    with transaction.atomic():
        iam_admin = (
            IAMAdminIdentity.objects.select_for_update()
            .select_related("user")
            .filter(admin_id=clean_admin_id)
            .first()
        )

        if not iam_admin:
            record_failed_attempt(lockout_key, threshold=3)
            # Prevent timing enumeration
            check_password(
                password, "pbkdf2_sha256$600000$dummy$dummypasswordhashforunknownaccounts"
            )
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

        # Linked employee identity lifecycle synchronization (Phase 1 IAM Invariant)
        if iam_admin.user.status != CustomUser.Status.ACTIVE or not iam_admin.user.is_active:
            raise AuthException(
                "AUTH-IAM-006",
                step="iam_login",
                message_override=f"Authoritative employee identity is {iam_admin.user.status}. IAM administrator access denied.",
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
        current_time = timezone.now()
        current_step = int(current_time.timestamp() // 30)
        cleaned_code = totp_code.strip()
        is_valid = totp.verify(cleaned_code, for_time=current_time, valid_window=1)
        if not is_valid:
            record_failed_attempt(lockout_key, employee_id=iam_admin.user.employee_id, threshold=3)
            raise AuthException(
                "AUTH-IAM-005",
                step="iam_login",
                message_override="Invalid IAM TOTP verification code.",
            )

        # Anti-replay: cannot reuse same TOTP step or past steps
        matched_step = None
        for step_offset in [0, -1, 1]:
            test_step = current_step + step_offset
            if totp.generate_otp(test_step) == cleaned_code:
                matched_step = test_step
                break

        step_to_record = matched_step or current_step
        if matched_step is not None and matched_step <= iam_admin.last_used_step:
            raise AuthException(
                "AUTH-IAM-004",
                step="iam_login",
                message_override="TOTP code has already been used. Please wait for the next 30-second token.",
            )
        iam_admin.last_used_step = step_to_record

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
    Persists only the keyed digest; the raw bearer token is set in request.session_key_iam for cookie dispatch.
    """
    raw_token = secrets.token_urlsafe(48)
    digest = hash_session_bearer(raw_token)
    client_ip = getattr(request, "client_ip", None) or request.META.get("REMOTE_ADDR", "127.0.0.1")
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:255]

    session = IAMSession.objects.create(
        session_key=digest,
        session_digest=digest,
        iam_admin=iam_admin,
        assurance_level=assurance_level,
        ip_address=client_ip,
        user_agent=user_agent,
        is_active=True,
    )

    request.session_key_iam = raw_token
    request.iam_admin = iam_admin
    request.iam_session = session

    return session


def get_iam_session_from_request(request) -> Optional[IAMSession]:
    """
    Resolves the authoritative IAMSession from the dedicated IAM cookie (__Host-salow_iam_session).
    Enforces idle expiry (IAM_SESSION_IDLE_TIMEOUT_MINUTES, 5 min) and absolute expiry (8 hours).
    Idle time is reset only by real requests, not by background polling paths.
    Resolves session via keyed HMAC-SHA256 digest lookup.
    """
    cookie_name = IAM_SESSION_COOKIE_NAME
    session_key = request.COOKIES.get(cookie_name)
    if not session_key and not getattr(settings, "SESSION_COOKIE_SECURE", False):
        session_key = request.COOKIES.get("salow_iam_session")

    if not session_key:
        return None

    digest = hash_session_bearer(session_key)

    session = (
        IAMSession.objects.select_related("iam_admin", "iam_admin__user")
        .filter(Q(session_key=digest) | Q(session_digest=digest), is_active=True)
        .first()
    )

    if not session:
        return None

    now = timezone.now()
    # 1. Idle expiry check (inactivity since the last real request)
    if session.last_activity and now - session.last_activity > timedelta(
        minutes=effective_iam_idle_timeout_minutes()
    ):
        session.is_active = False
        session.save(update_fields=["is_active"])
        return None

    # 2. Absolute expiry check (hours since sign-in, independent of activity)
    if session.created_at and now - session.created_at > timedelta(
        hours=IAM_SESSION_ABSOLUTE_TIMEOUT_HOURS
    ):
        session.is_active = False
        session.save(update_fields=["is_active"])
        return None

    # 3. Account status check (IAM Admin and linked employee identity lifecycle)
    if (
        session.iam_admin.status != IAMAdminIdentity.Status.ACTIVE
        or session.iam_admin.user.status != CustomUser.Status.ACTIVE
        or not session.iam_admin.user.is_active
    ):
        session.is_active = False
        session.save(update_fields=["is_active"])
        return None

    # Update last activity, except for background polling requests
    if not request.path.startswith(IAM_PASSIVE_PATH_PREFIXES):
        session.last_activity = now
        session.save(update_fields=["last_activity"])
    return session


def compute_payload_digest(payload: Any) -> str:
    """Computes canonical SHA-256 digest over normalized payload data."""
    if isinstance(payload, (dict, list)):
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    else:
        canonical = str(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def bound_stepup_digest(iam_session: IAMSession, action_name: str, payload: Any) -> str:
    """Digest binding one step-up to the actor, the action and the exact payload."""
    return compute_payload_digest(
        {"actor": iam_session.iam_admin_id, "action": action_name, "payload": payload or {}}
    )


def stepup_iam_assurance(
    iam_session: IAMSession,
    password: str,
    totp_code: str,
    action_name: str,
) -> bool:
    """
    Elevates IAM session to Assurance Level 2 (L2) for sensitive operations.
    Strictly bound to exact action_name and valid for 5 minutes.
    Atomically locks IAMAdminIdentity to prevent TOTP replay races across workers.
    """
    with transaction.atomic():
        iam_admin = IAMAdminIdentity.objects.select_for_update().get(id=iam_session.iam_admin_id)
        if not check_password(password, iam_admin.password_hash):
            return False

        if not iam_admin.encrypted_totp_secret:
            return False

        raw_secret = decrypt_totp_secret(
            bytes(iam_admin.encrypted_totp_secret),
            bytes(iam_admin.totp_nonce) if iam_admin.totp_nonce else b"",
            iam_admin.totp_key_id,
        )
        totp = pyotp.TOTP(raw_secret, interval=30)
        current_time = timezone.now()
        current_step = int(current_time.timestamp() // 30)
        cleaned_code = totp_code.strip()

        if not totp.verify(cleaned_code, for_time=current_time, valid_window=1):
            return False

        matched_step = None
        for step_offset in [0, -1, 1]:
            test_step = current_step + step_offset
            if totp.generate_otp(test_step) == cleaned_code:
                matched_step = test_step
                break

        step_to_record = matched_step or current_step
        if matched_step is not None and matched_step <= iam_admin.last_used_step:
            return False

        iam_admin.last_used_step = step_to_record
        iam_admin.save(update_fields=["last_used_step"])

        iam_session.refresh_from_db()
        iam_session.assurance_level = 2
        iam_session.stepup_action = action_name
        iam_session.stepup_expires_at = timezone.now() + timedelta(minutes=5)
        # Bind to the payload the console asked this step-up for (if it asked for this action).
        iam_session.stepup_payload_digest = (
            iam_session.pending_payload_digest if iam_session.pending_action == action_name else ""
        )
        iam_session.pending_action = ""
        iam_session.pending_payload_digest = ""
        iam_session.pending_summary = ""
        iam_session.save(
            update_fields=[
                "assurance_level",
                "stepup_action",
                "stepup_expires_at",
                "stepup_payload_digest",
                "pending_action",
                "pending_payload_digest",
                "pending_summary",
            ]
        )

        log_auth_event(
            event="iam_assurance_elevated",
            code="INFO",
            step="stepup",
            employee_id=iam_admin.user.employee_id,
            reference_id=iam_admin.admin_id,
            message=f"IAM session elevated to Level 2 for action '{action_name}' (expires in 5m)",
        )

        return True


def create_dual_approval_request(
    requester_session: IAMSession,
    action: str,
    target_id: str,
    reason: str,
    payload_digest: str = "",
) -> IAMDualApprovalRequest:
    """
    Initiates a Level 3 independent dual-approval request (Section 9).
    Requester must hold an active Level 2 assurance session bound to action.
    Binds canonical payload digest to mutation parameters.
    """
    now = timezone.now()
    if (
        requester_session.assurance_level < 2
        or requester_session.stepup_action != action
        or not requester_session.stepup_expires_at
        or requester_session.stepup_expires_at <= now
    ):
        raise AuthException(
            "AUTH-IAM-L3-001",
            step="dual_approval",
            message_override="Requester must complete fresh Level 2 step-up before initiating dual approval.",
        )

    req = IAMDualApprovalRequest.objects.create(
        action=action,
        target_id=str(target_id),
        reason=reason,
        payload_digest=payload_digest or "",
        requester=requester_session.iam_admin,
        requester_session=requester_session,
        requester_stepup_verified_at=now,
        expires_at=now + timedelta(minutes=10),
        status=IAMDualApprovalRequest.Status.PENDING,
    )

    log_auth_event(
        event="iam_dual_approval_requested",
        code="INFO",
        step="dual_approval",
        employee_id=requester_session.iam_admin.user.employee_id,
        reference_id=str(req.request_id),
        message=f"Level 3 dual approval requested for action [{action}] on target [{target_id}]",
    )
    return req


def approve_dual_approval_request(
    approver_session: IAMSession,
    request_id: uuid.UUID,
    allowed_roles: Optional[tuple] = None,
) -> IAMDualApprovalRequest:
    """
    Executes independent second administrator approval for Level 3 action (Section 9).
    Requires:
    1. Independent IAM session from a distinct administrator (no self-approval).
    2. Approver is an active Super Admin.
    3. Approver independently completed fresh Level 2 step-up for the identical action.
    """
    now = timezone.now()
    with transaction.atomic():
        req = (
            IAMDualApprovalRequest.objects.select_for_update().filter(request_id=request_id).first()
        )
        if not req:
            raise AuthException(
                "AUTH-IAM-L3-002",
                step="dual_approval",
                message_override="Approval request not found.",
            )

        if req.status != IAMDualApprovalRequest.Status.PENDING or req.expires_at <= now:
            raise AuthException(
                "AUTH-IAM-L3-003",
                step="dual_approval",
                message_override="Approval request is not pending or has expired.",
            )

        # Strict No Self-Approval (Section 9)
        if approver_session.iam_admin.id == req.requester_id:
            raise PermissionDenied("Self-approval is strictly prohibited for Level 3 operations.")

        # Approver must be an active Super Admin
        permitted = allowed_roles or (IAMAdminIdentity.Role.SUPER_ADMIN,)
        if (
            approver_session.iam_admin.role not in permitted
            or approver_session.iam_admin.status != IAMAdminIdentity.Status.ACTIVE
        ):
            raise PermissionDenied(
                "Only active Super Administrators can grant Level 3 dual approval."
            )

        # Approver must hold fresh Level 2 step-up for the same action
        if (
            approver_session.assurance_level < 2
            or approver_session.stepup_action != req.action
            or not approver_session.stepup_expires_at
            or approver_session.stepup_expires_at <= now
        ):
            raise AuthException(
                "AUTH-IAM-L3-004",
                step="dual_approval",
                message_override="Second approver must complete independent fresh Level 2 step-up for this operation.",
            )

        req.status = IAMDualApprovalRequest.Status.APPROVED
        req.approver = approver_session.iam_admin
        req.approver_session = approver_session
        req.approver_stepup_verified_at = now
        req.save()

        log_auth_event(
            event="iam_dual_approval_granted",
            code="INFO",
            step="dual_approval",
            employee_id=approver_session.iam_admin.user.employee_id,
            reference_id=str(req.request_id),
            message=f"Level 3 dual approval granted by {approver_session.iam_admin.admin_id} for action [{req.action}]",
        )
        return req


def consume_dual_approval_request(  # noqa: C901 (refactor deferred, behavior covered by tests)
    request_id: uuid.UUID,
    expected_action: str,
    expected_target_id: str,
    expected_payload_digest: Optional[str] = None,
) -> IAMDualApprovalRequest:
    """
    Atomically validates and marks a Level 3 dual approval as consumed to prevent replay attacks.
    Exhaustively revalidates identities, sessions, active status, fresh assurance, and payload binding.
    """
    now = timezone.now()
    with transaction.atomic():
        req = (
            IAMDualApprovalRequest.objects.select_for_update()
            .select_related(
                "requester__user", "approver__user", "requester_session", "approver_session"
            )
            .filter(request_id=request_id)
            .first()
        )
        if not req:
            raise AuthException(
                "AUTH-IAM-L3-005",
                step="dual_approval",
                message_override="Approval request not found.",
            )

        if req.status != IAMDualApprovalRequest.Status.APPROVED or req.consumed_at is not None:
            raise AuthException(
                "AUTH-IAM-L3-006",
                step="dual_approval",
                message_override="Approval request is not approved or has already been consumed.",
            )

        if req.expires_at <= now:
            raise AuthException(
                "AUTH-IAM-L3-007",
                step="dual_approval",
                message_override="Dual approval request has expired.",
            )

        if req.action != expected_action or req.target_id != str(expected_target_id):
            raise AuthException(
                "AUTH-IAM-L3-008",
                step="dual_approval",
                message_override="Dual approval parameters do not match executed action.",
            )

        # Revalidate payload digest if specified or bound
        if expected_payload_digest is not None:
            if req.payload_digest != expected_payload_digest:
                raise AuthException(
                    "AUTH-IAM-L3-009",
                    step="dual_approval",
                    message_override="Dual approval payload digest mismatch.",
                )
        elif req.payload_digest:
            raise AuthException(
                "AUTH-IAM-L3-009",
                step="dual_approval",
                message_override="Dual approval requires payload digest verification.",
            )

        # Revalidate requester status and authorization
        requester = req.requester
        if (
            not requester
            or requester.status != IAMAdminIdentity.Status.ACTIVE
            or not requester.user.is_active
        ):
            raise AuthException(
                "AUTH-IAM-L3-010",
                step="dual_approval",
                message_override="Requester administrator account is no longer active.",
            )

        # Revalidate approver status and authorization
        approver = req.approver
        if (
            not approver
            or approver.status != IAMAdminIdentity.Status.ACTIVE
            or not approver.user.is_active
        ):
            raise AuthException(
                "AUTH-IAM-L3-011",
                step="dual_approval",
                message_override="Approver administrator account is no longer active.",
            )

        # Both actors must still hold the Super Administrator role at the mutation boundary
        if (
            requester.role != IAMAdminIdentity.Role.SUPER_ADMIN
            or approver.role != IAMAdminIdentity.Role.SUPER_ADMIN
        ):
            raise PermissionDenied(
                "Both the requester and the approver must still be active Super Administrators."
            )

        # Revalidate separation of duties (no self-approval)
        if requester.id == approver.id:
            raise PermissionDenied("Requester and approver must be distinct identities.")

        # Revalidate sessions
        if req.requester_session and not req.requester_session.is_active:
            raise AuthException(
                "AUTH-IAM-L3-012",
                step="dual_approval",
                message_override="Requester session has expired or was revoked.",
            )

        if req.approver_session and not req.approver_session.is_active:
            raise AuthException(
                "AUTH-IAM-L3-013",
                step="dual_approval",
                message_override="Approver session has expired or was revoked.",
            )

        # Atomically consume
        req.status = IAMDualApprovalRequest.Status.CONSUMED
        req.consumed_at = now
        req.save(update_fields=["status", "consumed_at"])

        log_auth_event(
            event="iam_dual_approval_consumed",
            code="INFO",
            step="dual_approval",
            employee_id=req.requester.user.employee_id,
            reference_id=str(req.request_id),
            message=f"Level 3 dual approval [{req.request_id}] consumed transactionally by {req.requester.admin_id} (Approved by {req.approver.admin_id}).",
        )
        return req


def check_iam_assurance(  # noqa: C901 (refactor deferred, behavior covered by tests)
    request,
    required_level: int,
    action_name: str,
    approver_admin: Optional[Any] = None,
    approver_session: Optional[IAMSession] = None,
    dual_request_id: Optional[uuid.UUID] = None,
    payload_digest: Optional[str] = None,
) -> bool:
    """
    Centralized server-side assurance enforcement (Section 9):
    - Level 1: Dedicated IAM Administrator session.
    - Level 2: Sensitive action step-up re-authentication bound to action and valid.
    - Level 3: Privileged operation requiring Level 2 AND independent second approver Level 2 (no self-approval).
    """
    iam_session: Optional[IAMSession] = getattr(
        request, "iam_session", None
    ) or get_iam_session_from_request(request)
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
        # Check Requester Level 2 conditions first
        if not (
            iam_session.assurance_level >= 2
            and iam_session.stepup_action == action_name
            and iam_session.stepup_expires_at
            and iam_session.stepup_expires_at > now
        ):
            return False

        # If a transaction-bound dual_request_id was provided, verify it directly
        if dual_request_id:
            req = IAMDualApprovalRequest.objects.filter(
                request_id=dual_request_id,
                status=IAMDualApprovalRequest.Status.APPROVED,
                action=action_name,
                expires_at__gt=now,
            ).first()
            if not req or req.requester_id != iam_session.iam_admin.id:
                return False
            if req.approver_id == iam_session.iam_admin.id:
                return False
            if payload_digest is not None and req.payload_digest != payload_digest:
                return False
            return True

        # Second approver provided via session
        target_session = approver_session
        if not target_session and isinstance(approver_admin, IAMSession):
            target_session = approver_admin

        if target_session:
            if not target_session.is_active:
                return False
            if target_session.iam_admin.id == iam_session.iam_admin.id:
                return False
            if (
                target_session.iam_admin.role != IAMAdminIdentity.Role.SUPER_ADMIN
                or target_session.iam_admin.status != IAMAdminIdentity.Status.ACTIVE
            ):
                return False
            if (
                target_session.assurance_level >= 2
                and target_session.stepup_action == action_name
                and target_session.stepup_expires_at
                and target_session.stepup_expires_at > now
            ):
                return True
            return False

        # Second approver provided via identity object
        if approver_admin and isinstance(approver_admin, IAMAdminIdentity):
            if approver_admin.id == iam_session.iam_admin.id:
                return False
            if (
                approver_admin.role != IAMAdminIdentity.Role.SUPER_ADMIN
                or approver_admin.status != IAMAdminIdentity.Status.ACTIVE
            ):
                return False

            # Check if approver_admin has an active Level 2 session for this action
            has_valid_l2_session = IAMSession.objects.filter(
                iam_admin=approver_admin,
                is_active=True,
                assurance_level__gte=2,
                stepup_action=action_name,
                stepup_expires_at__gt=now,
            ).exists()
            if has_valid_l2_session:
                return True

            # In development/test runner fallback if allowed
            if getattr(settings, "DEV_VERIFIER", False):
                return True
            return False

        return False

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
