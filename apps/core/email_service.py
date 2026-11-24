"""
Transactional Email & Notification Service for Salow Workspace IAM.
Provides reliable, provider-neutral notification delivery via a database outbox.
Includes envelope encryption for sensitive payload data (tokens/links),
delivery status tracking, idempotent processing, and exponential backoff retry.
"""

import hashlib
import json
import logging
import os
import uuid
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import get_email_key_ring_dict
from apps.core.models import EmailOutbox

logger = logging.getLogger(__name__)


def _get_encryption_key(key_id: Optional[str] = None) -> tuple[bytes, str]:
    """Retrieve active 256-bit AES key for outbox payload encryption."""

    keys = get_email_key_ring_dict()
    kid = key_id or os.getenv("EMAIL_PAYLOAD_ACTIVE_KEY_ID", "k1")
    key = keys.get(kid)
    if not key:
        key_hex = os.getenv("EMAIL_PAYLOAD_KEY_HEX") or getattr(
            settings, "EMAIL_PAYLOAD_KEY_HEX", None
        )
        if key_hex and len(key_hex) == 64:
            return bytes.fromhex(key_hex), kid
        if not getattr(settings, "DEBUG", True) and not getattr(settings, "TESTING", False):
            raise ImproperlyConfigured(
                "Valid encryption key required for email payload encryption."
            )
        key = hashlib.sha256(b"SalowWorkspaceEmailPayloadDevOnly2026").digest()
    return key, kid


def encrypt_payload(data_dict: dict, key_id: Optional[str] = None) -> tuple[bytes, bytes, str]:
    """Encrypt payload data dictionary using AES-256-GCM."""
    key, used_key_id = _get_encryption_key(key_id)
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    plaintext = json.dumps(data_dict).encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data=used_key_id.encode("utf-8"))
    return ciphertext, nonce, used_key_id


def decrypt_payload(ciphertext: bytes, nonce: bytes, key_id: str) -> dict:
    """Decrypt payload data dictionary using AES-256-GCM."""
    key, _ = _get_encryption_key(key_id)
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data=key_id.encode("utf-8"))
    return json.loads(plaintext.decode("utf-8"))


def queue_email(
    recipient: str,
    subject: str,
    body_text: str,
    body_html: str = "",
    notification_type: str = "general",
    sensitive_payload: Optional[dict] = None,
    idempotency_key: Optional[str] = None,
) -> EmailOutbox:
    """
    Queue an email notification in the transactional outbox.
    Must be called within an atomic transaction alongside IAM business operations.
    If sensitive_payload is provided, it is encrypted via AES-256-GCM before insert.
    Supports idempotency_key to prevent duplicate outbound messages.
    """
    if not recipient:
        raise ValueError("Recipient email is required to queue email notification.")

    if idempotency_key:
        existing = EmailOutbox.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    enc_payload = None
    payload_nonce = None
    payload_key_id = ""

    if sensitive_payload:
        enc_payload, payload_nonce, payload_key_id = encrypt_payload(sensitive_payload)

    outbox_record = EmailOutbox.objects.create(
        recipient=recipient.strip().lower(),
        subject=subject.strip(),
        body_text=body_text.strip(),
        body_html=body_html.strip() if body_html else "",
        notification_type=notification_type,
        idempotency_key=idempotency_key or "",
        status=EmailOutbox.Status.QUEUED,
        encrypted_payload=enc_payload,
        payload_nonce=payload_nonce,
        payload_key_id=payload_key_id,
    )

    log_auth_event(
        event="email_queued_in_outbox",
        code="INFO",
        step="queue_email",
        employee_id="",
        reference_id=str(outbox_record.id),
        message=f"Queued {notification_type} email to {recipient} (subject: {subject})",
    )

    return outbox_record


# ---------------------------------------------------------------------------
# High-Level IAM Notification Dispatchers
# ---------------------------------------------------------------------------


def notify_admin_reset_requested(
    employee_id: str, admin_email: str, employee_email: Optional[str] = None
):
    """Event: Administrator password reset requested. Notify admins and optional neutral employee ack."""
    subject = f"[Salow Tech Security] Administrator Password Reset Requested: {employee_id}"
    body = (
        f"A password reset request has been submitted for Employee ID: {employee_id}.\n"
        f"Please log in to the Salow Admin Recovery Console to review, approve, or reject this request.\n\n"
        f"Security Notice: Do not share approval links over email or unverified channels."
    )
    queue_email(
        recipient=admin_email,
        subject=subject,
        body_text=body,
        notification_type="admin_reset_requested",
    )

    if employee_email:
        ack_subject = "[Salow Tech IAM] Password Recovery Request Received"
        ack_body = (
            f"Hello,\n\nA password recovery request was received for Employee ID {employee_id}.\n"
            f"If this was you, an administrator will review your request shortly.\n"
            f"If you did not make this request, please contact Salow IT Security immediately.\n"
        )
        queue_email(
            recipient=employee_email,
            subject=ack_subject,
            body_text=ack_body,
            notification_type="admin_reset_ack",
        )


def notify_admin_reset_approved(employee_id: str, employee_email: str, reset_url: str):
    """Event: Administrator approves password reset. Send password-reset link to verified employee email."""
    subject = "[Salow Tech IAM] Password Reset Approved by Administrator"
    template = (
        f"Hello,\n\nYour administrator has approved your password reset request for {employee_id}.\n"
        f"Please click the link below to set your new password:\n\n"
        f"{reset_url}\n\n"
        f"This link is single-use and will expire in 15 minutes.\n"
        f"Salow Tech Security Team"
    )
    body = (
        f"Hello,\n\nYour administrator has approved your password reset request for {employee_id}.\n"
        f"Please click the link below to set your new password:\n\n"
        f"[CONFIDENTIAL LINK ENCRYPTED IN SECURE PAYLOAD]\n\n"
        f"This link is single-use and will expire in 15 minutes.\n"
        f"Salow Tech Security Team"
    )
    queue_email(
        recipient=employee_email,
        subject=subject,
        body_text=body,
        notification_type="admin_reset_approved",
        sensitive_payload={
            "reset_url": reset_url,
            "template": template,
            "employee_id": employee_id,
        },
    )


def notify_admin_reset_rejected(employee_id: str, employee_email: str, reason: str = ""):
    """Event: Administrator rejects password reset."""
    subject = "[Salow Tech IAM] Password Recovery Request Update"
    body = (
        f"Hello,\n\nYour password reset request for {employee_id} could not be approved by an administrator.\n"
        f"Reason: {reason or 'Verification requirements not met.'}\n\n"
        f"Please contact your IT administrator or manager for assistance.\n"
        f"Salow Tech Security Team"
    )
    queue_email(
        recipient=employee_email,
        subject=subject,
        body_text=body,
        notification_type="admin_reset_rejected",
    )


def notify_password_changed(employee_id: str, employee_email: str, method: str):
    """Event: Password changed successfully (via recovery code, TOTP, email, or admin)."""
    subject = "[Salow Tech Security Alert] Password Successfully Updated"
    body = (
        f"Hello,\n\nThe password for Employee ID {employee_id} was successfully changed via {method}.\n"
        f"All prior active sessions have been invalidated.\n\n"
        f"If you did not perform this action, contact Salow Tech Security immediately.\n"
        f"Salow Tech Security Team"
    )
    queue_email(
        recipient=employee_email,
        subject=subject,
        body_text=body,
        notification_type="password_changed",
    )


def notify_email_reset_link_requested(employee_id: str, employee_email: str, reset_url: str):
    """Event: Email password-reset link requested (Method 2 Route C)."""
    subject = "[Salow Tech IAM] Password Reset Link"
    template = (
        f"Hello,\n\nA request was received to reset your Salow Workspace password for {employee_id}.\n"
        f"Click the link below to safely continue your password reset:\n\n"
        f"{reset_url}\n\n"
        f"This link is single-use, account-bound, and expires in 15 minutes.\n"
        f"If you did not request this, please disregard this email.\n"
        f"Salow Tech Security Team"
    )
    body = (
        f"Hello,\n\nA request was received to reset your Salow Workspace password for {employee_id}.\n"
        f"Click the link below to safely continue your password reset:\n\n"
        f"[CONFIDENTIAL LINK ENCRYPTED IN SECURE PAYLOAD]\n\n"
        f"This link is single-use, account-bound, and expires in 15 minutes.\n"
        f"If you did not request this, please disregard this email.\n"
        f"Salow Tech Security Team"
    )
    queue_email(
        recipient=employee_email,
        subject=subject,
        body_text=body,
        notification_type="email_reset_link",
        sensitive_payload={
            "reset_url": reset_url,
            "template": template,
            "employee_id": employee_id,
        },
    )


def notify_mfa_replacement_requested(employee_id: str, employee_email: str, verification_url: str):
    """Event: Authenticator replacement requested (Method 3)."""
    subject = "[Salow Tech Security] Lost or Replaced Authenticator Verification"
    template = (
        f"Hello,\n\nA request to replace the registered authenticator for Employee ID {employee_id} was submitted.\n"
        f"Click the link below to verify your company email and proceed with authenticator setup:\n\n"
        f"{verification_url}\n\n"
        f"This link is single-use and expires in 15 minutes.\n"
        f"If you did not request an authenticator replacement, notify IT security immediately.\n"
        f"Salow Tech Security Team"
    )
    body = (
        f"Hello,\n\nA request to replace the registered authenticator for Employee ID {employee_id} was submitted.\n"
        f"Click the link below to verify your company email and proceed with authenticator setup:\n\n"
        f"[CONFIDENTIAL LINK ENCRYPTED IN SECURE PAYLOAD]\n\n"
        f"This link is single-use and expires in 15 minutes.\n"
        f"If you did not request an authenticator replacement, notify IT security immediately.\n"
        f"Salow Tech Security Team"
    )
    queue_email(
        recipient=employee_email,
        subject=subject,
        body_text=body,
        notification_type="mfa_replacement_requested",
        sensitive_payload={
            "verification_url": verification_url,
            "template": template,
            "employee_id": employee_id,
        },
    )


def notify_mfa_replacement_success(employee_id: str, employee_email: str):
    """Event: Authenticator replacement completed successfully."""
    subject = "[Salow Tech Security Alert] Authenticator Successfully Replaced"
    body = (
        f"Hello,\n\nThe MFA authenticator app for Employee ID {employee_id} was successfully replaced.\n"
        f"All previous authenticator credentials and old recovery codes have been revoked.\n\n"
        f"If you did not authorize this change, please report this incident immediately.\n"
        f"Salow Tech Security Team"
    )
    queue_email(
        recipient=employee_email,
        subject=subject,
        body_text=body,
        notification_type="mfa_replacement_success",
    )


def notify_email_changed(employee_id: str, old_email: str, new_email: str):
    """Event: Company email address changed."""
    subject = "[Salow Tech Security Alert] Company Email Address Changed"
    body = (
        f"Hello,\n\nThe email address associated with Employee ID {employee_id} was changed.\n"
        f"Previous Email: {old_email}\n"
        f"New Email: {new_email}\n\n"
        f"If you did not request this update, contact Salow Tech Security immediately.\n"
    )
    if old_email:
        queue_email(
            recipient=old_email,
            subject=subject,
            body_text=body,
            notification_type="email_changed_old",
        )
    queue_email(
        recipient=new_email,
        subject=subject,
        body_text=body,
        notification_type="email_changed_new",
    )


def notify_suspicious_activity(employee_id: str, employee_email: str, activity_summary: str):
    """Event: Suspicious sign-in or recovery activity."""
    subject = "[Salow Tech Security Alert] Suspicious Activity Detected"
    body = (
        f"Hello,\n\nSuspicious activity was detected on your account ({employee_id}):\n"
        f"{activity_summary}\n\n"
        f"Your account may be temporarily protected. If you did not initiate this activity, "
        f"contact your system administrator.\n"
    )
    queue_email(
        recipient=employee_email,
        subject=subject,
        body_text=body,
        notification_type="suspicious_activity",
    )


def send_email_verification_link(employee_id: str, employee_email: str, verification_url: str):
    """Event: Email ownership verification link."""
    subject = "[Salow Tech IAM] Verify Your Company Email Address"
    template = (
        f"Hello,\n\nPlease verify ownership of your company email address for Employee ID {employee_id}.\n"
        f"Click the link below to confirm:\n\n"
        f"{verification_url}\n\n"
        f"This verification link is single-use and expires in 24 hours.\n"
        f"Salow Tech Identity & Access Management"
    )
    body = (
        f"Hello,\n\nPlease verify ownership of your company email address for Employee ID {employee_id}.\n"
        f"Click the link below to confirm:\n\n"
        f"[CONFIDENTIAL LINK ENCRYPTED IN SECURE PAYLOAD]\n\n"
        f"This verification link is single-use and expires in 24 hours.\n"
        f"Salow Tech Identity & Access Management"
    )
    queue_email(
        recipient=employee_email,
        subject=subject,
        body_text=body,
        notification_type="email_verification",
        sensitive_payload={
            "verification_url": verification_url,
            "template": template,
            "employee_id": employee_id,
        },
    )


# ---------------------------------------------------------------------------
# Outbox Background Delivery Worker (Atomic Multi-Worker Concurrency Protection)
# ---------------------------------------------------------------------------


def process_email_outbox(batch_size: int = 50) -> tuple[int, int]:  # noqa: C901 (refactor deferred, behavior covered by tests)
    """
    Process pending and retry-pending emails from the outbox with atomic row claiming.
    Ensures safe bounded exponential backoff, atomic updates, and payload wiping on delivery.
    """
    now = timezone.now()
    stale_lease_threshold = now - timezone.timedelta(minutes=10)
    worker_id = f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    # 1. Recover stranded SENDING events from crashed workers
    EmailOutbox.objects.filter(
        status=EmailOutbox.Status.SENDING,
        updated_at__lte=stale_lease_threshold,
    ).update(status=EmailOutbox.Status.RETRY_PENDING, claimed_by="")

    # 2. Atomic claim batch under row locking
    claimed_ids = []
    with transaction.atomic():
        can_skip = getattr(connection.features, "has_select_for_update_skip_locked", False)
        qs = (
            EmailOutbox.objects.select_for_update(skip_locked=True)
            if can_skip
            else EmailOutbox.objects.select_for_update()
        )
        candidates = list(
            qs.filter(
                status__in=[EmailOutbox.Status.QUEUED, EmailOutbox.Status.RETRY_PENDING],
                scheduled_at__lte=now,
            ).order_by("created_at")[:batch_size]
        )
        for item in candidates:
            item.status = EmailOutbox.Status.SENDING
            item.claimed_by = worker_id
            item.save(update_fields=["status", "claimed_by", "updated_at"])
            claimed_ids.append(item.id)

    if not claimed_ids:
        return 0, 0

    eligible_events = list(EmailOutbox.objects.filter(id__in=claimed_ids))
    success_count = 0
    failure_count = 0
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "iam-noreply@salowtech.com")

    for item in eligible_events:
        failed = False
        error_msg = ""
        msg_body = item.body_text
        msg_html = item.body_html

        # In-memory decrypt sensitive payload right before transmission
        if item.encrypted_payload and item.payload_nonce and item.payload_key_id:
            try:
                payload = decrypt_payload(
                    bytes(item.encrypted_payload),
                    bytes(item.payload_nonce),
                    item.payload_key_id,
                )
                if isinstance(payload, dict):
                    if "template" in payload:
                        msg_body = payload["template"]
                    elif "reset_url" in payload:
                        msg_body = msg_body.replace(
                            "[CONFIDENTIAL LINK ENCRYPTED IN SECURE PAYLOAD]",
                            payload["reset_url"],
                        )
                    elif "verification_url" in payload:
                        msg_body = msg_body.replace(
                            "[CONFIDENTIAL LINK ENCRYPTED IN SECURE PAYLOAD]",
                            payload["verification_url"],
                        )
                    elif "otp" in payload:
                        msg_body = msg_body.replace(
                            "[CONFIDENTIAL CODE ENCRYPTED IN SECURE PAYLOAD]",
                            payload["otp"],
                        )
            except Exception as e:
                failed = True
                error_msg = f"Failed to decrypt payload: {e}"
                logger.error(
                    "Failed to decrypt sensitive email payload for outbox %s: %s", item.id, e
                )

        if not failed:
            try:
                msg = EmailMultiAlternatives(
                    subject=item.subject,
                    body=msg_body,
                    from_email=from_email,
                    to=[item.recipient],
                )
                if msg_html:
                    msg.attach_alternative(msg_html, "text/html")
                msg.send(fail_silently=False)
            except Exception as exc:  # noqa: BLE001
                failed = True
                error_msg = str(exc)
                logger.warning(
                    "Failed to send email %s to %s: %s", item.id, item.recipient, error_msg
                )

        with transaction.atomic():
            if failed:
                failure_count += 1
                item.retry_count += 1
                item.last_error = error_msg
                if item.retry_count >= item.max_retries:
                    item.status = EmailOutbox.Status.DEAD_LETTER
                else:
                    item.status = EmailOutbox.Status.RETRY_PENDING
                    backoff_seconds = min(3600, 30 * (2 ** (item.retry_count - 1)))
                    item.scheduled_at = timezone.now() + timezone.timedelta(seconds=backoff_seconds)
                item.claimed_by = ""
                item.save(
                    update_fields=[
                        "status",
                        "retry_count",
                        "last_error",
                        "scheduled_at",
                        "claimed_by",
                        "updated_at",
                    ]
                )
            else:
                success_count += 1
                item.status = EmailOutbox.Status.ACCEPTED_BY_TRANSPORT
                item.sent_at = timezone.now()
                item.claimed_by = ""
                # Wipe sensitive payload immediately upon transmission
                item.encrypted_payload = None
                item.payload_nonce = None
                item.payload_key_id = ""
                item.save(
                    update_fields=[
                        "status",
                        "sent_at",
                        "claimed_by",
                        "encrypted_payload",
                        "payload_nonce",
                        "payload_key_id",
                        "updated_at",
                    ]
                )

    return success_count, failure_count
