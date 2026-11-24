import hashlib
import hmac
import secrets
import uuid
from datetime import date
from typing import Optional, Tuple

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from apps.accounts.auth.config import get_activation_hmac_key
from apps.accounts.models import CustomUser
from apps.accounts.permissions import (
    PERM_CERTIFICATE_ISSUE,
    PERM_CERTIFICATE_REVOKE,
    has_permission,
)
from apps.certificates.models import (
    Certificate,
    CertificateTemplate,
    CertificateVerificationLog,
)
from apps.core.models import OutboxEvent
from apps.core.services import record_audit_event


def compute_certificate_hash(
    serial_number: str,
    cert_uuid_str: str,
    employee_id: str,
    issue_date_str: str,
    title: str = "",
    recipient_name: str = "",
) -> str:
    """
    Generate cryptographically verifiable HMAC-SHA256 tamper-evident digest.
    Guarantees any mutation to recipient, serial, date, title, or identifier breaks validation.
    """
    canonical_payload = (
        f"{serial_number.strip()}:{cert_uuid_str.strip()}:"
        f"{employee_id.strip()}:{issue_date_str.strip()}:"
        f"{title.strip()}:{recipient_name.strip()}"
    )
    secret_key = get_activation_hmac_key()
    return hmac.new(secret_key, canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_serial_number() -> str:
    """Generate human-readable serial number formatted as ST-CERT-YYYY-XXXXXX."""
    current_year = timezone.now().year
    random_suffix = secrets.token_hex(3).upper()
    return f"ST-CERT-{current_year}-{random_suffix}"


def issue_certificate(
    *,
    recipient: CustomUser,
    title: str,
    description: str,
    issued_by: CustomUser,
    template: Optional[CertificateTemplate] = None,
    recipient_role: str = "",
    issue_date: Optional[date] = None,
    expiry_date: Optional[date] = None,
    supersedes: Optional[Certificate] = None,
) -> Certificate:
    """Issue official certificate with cryptographic verification hash.

    ``supersedes`` links a reissue to the revoked certificate it replaces. The old
    certificate must already be revoked, belong to the same recipient and not have been
    replaced before.
    """
    if not has_permission(issued_by, PERM_CERTIFICATE_ISSUE):
        raise PermissionDenied("User does not have permission to issue certificates.")
    if supersedes is not None:
        if not supersedes.is_revoked:
            raise ValidationError("Only a revoked certificate can be reissued. Revoke it first.")
        if supersedes.recipient_id != recipient.id:
            raise ValidationError("A reissue must go to the same recipient as the revoked one.")
        if Certificate.objects.filter(supersedes=supersedes).exists():
            raise ValidationError("This certificate has already been reissued.")

    if not title or not title.strip():
        raise ValidationError("Certificate title is required.")
    if not description or not description.strip():
        raise ValidationError("Certificate description is required.")

    if not issue_date:
        issue_date = (
            timezone.localdate() if timezone.is_aware(timezone.now()) else timezone.now().date()
        )

    cert_uuid = uuid.uuid4()
    serial_number = generate_serial_number()

    # Ensure uniqueness of serial
    while Certificate.objects.filter(serial_number=serial_number).exists():
        serial_number = generate_serial_number()

    recipient_display_name = recipient.full_name or recipient.employee_id
    v_hash = compute_certificate_hash(
        serial_number=serial_number,
        cert_uuid_str=str(cert_uuid),
        employee_id=recipient.employee_id,
        issue_date_str=str(issue_date),
        title=title,
        recipient_name=recipient_display_name,
    )

    with transaction.atomic():
        cert = Certificate.objects.create(
            serial_number=serial_number,
            uuid=cert_uuid,
            recipient=recipient,
            template=template,
            title=title.strip(),
            recipient_name=recipient_display_name,
            recipient_role=recipient_role or recipient.get_role_display(),
            description=description.strip(),
            issue_date=issue_date,
            expiry_date=expiry_date,
            issued_by=issued_by,
            verification_hash=v_hash,
            is_revoked=False,
            supersedes=supersedes,
        )

        record_audit_event(
            actor=issued_by,
            action="certificate_issued",
            target_type="certificate",
            target_id=cert.serial_number,
            payload_summary={
                "recipient_id": recipient.id,
                "recipient_employee_id": recipient.employee_id,
                "title": cert.title,
                "serial_number": cert.serial_number,
                "verification_hash": cert.verification_hash,
                "supersedes_serial": supersedes.serial_number if supersedes else "",
            },
        )

        OutboxEvent.objects.create(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            event_type="certificate.issued",
            payload={
                "certificate_id": cert.id,
                "serial_number": cert.serial_number,
                "recipient_id": recipient.id,
            },
        )

    return cert


def revoke_certificate(
    *,
    certificate: Certificate,
    revoked_by: CustomUser,
    reason: str,
) -> Certificate:
    """Revoke an issued certificate and record reason in audit log."""
    if not has_permission(revoked_by, PERM_CERTIFICATE_REVOKE):
        raise PermissionDenied("User does not have permission to revoke certificates.")

    if not reason or not reason.strip():
        raise ValidationError("A revocation reason must be provided.")

    if certificate.is_revoked:
        raise ValidationError("Certificate is already revoked.")

    with transaction.atomic():
        certificate.is_revoked = True
        certificate.revoked_at = timezone.now()
        certificate.revoked_by = revoked_by
        certificate.revocation_reason = reason.strip()
        certificate.save(
            update_fields=[
                "is_revoked",
                "revoked_at",
                "revoked_by",
                "revocation_reason",
                "updated_at",
            ]
        )

        record_audit_event(
            actor=revoked_by,
            action="certificate_revoked",
            target_type="certificate",
            target_id=certificate.serial_number,
            payload_summary={
                "serial_number": certificate.serial_number,
                "revoked_by_id": revoked_by.id,
                "reason": reason.strip(),
            },
        )

        OutboxEvent.objects.create(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            event_type="certificate.revoked",
            payload={
                "certificate_id": certificate.id,
                "serial_number": certificate.serial_number,
                "reason": reason.strip(),
            },
        )

    return certificate


def verify_certificate(
    identifier: str,
    ip_address: Optional[str] = None,
    user_agent: str = "",
) -> Tuple[Optional[Certificate], str]:
    """
    Public verification engine. Looks up certificate by serial, uuid, or verification hash.
    Returns (certificate, status) where status is 'valid', 'revoked', 'expired', or 'not_found'.
    Logs each verification inquiry to CertificateVerificationLog and AuditLog.
    """
    clean_id = identifier.strip()
    cert = (
        Certificate.objects.select_related("recipient", "issued_by", "template")
        .filter(serial_number__iexact=clean_id)
        .first()
    )
    if not cert:
        # Check by UUID
        try:
            val_uuid = uuid.UUID(clean_id)
            cert = (
                Certificate.objects.select_related("recipient", "issued_by", "template")
                .filter(uuid=val_uuid)
                .first()
            )
        except ValueError:
            pass

    if not cert:
        # Check by hash
        cert = (
            Certificate.objects.select_related("recipient", "issued_by", "template")
            .filter(verification_hash__iexact=clean_id)
            .first()
        )

    if not cert:
        return None, "not_found"

    # Log the verification attempt
    CertificateVerificationLog.objects.create(
        certificate=cert,
        ip_address=ip_address,
        user_agent=user_agent[:500],
    )

    record_audit_event(
        action="certificate_publicly_verified",
        target_type="certificate",
        target_id=cert.serial_number,
        payload_summary={
            "serial_number": cert.serial_number,
            "ip_address": ip_address or "unknown",
            "is_revoked": cert.is_revoked,
        },
    )

    if cert.is_revoked:
        return cert, "revoked"

    if cert.expiry_date and timezone.localdate() > cert.expiry_date:
        return cert, "expired"

    # Verify cryptographic hash integrity
    expected_hash = compute_certificate_hash(
        serial_number=cert.serial_number,
        cert_uuid_str=str(cert.uuid),
        employee_id=cert.recipient.employee_id,
        issue_date_str=str(cert.issue_date),
        title=cert.title,
        recipient_name=cert.recipient_name,
    )
    if not hmac.compare_digest(cert.verification_hash, expected_hash):
        return cert, "tampered"

    return cert, "valid"


def generate_certificate_svg(cert: Certificate) -> str:
    """
    Generate print-ready, high-resolution vector SVG of certificate.
    Uses official Salowtech brand colors: #AD8B73, #FFFBE9, #2A241F.
    Standard A4 landscape dimensions (842 x 595).
    Safely XML-escapes all dynamic content and avoids non-XML entities.
    """
    status_watermark = ""
    if cert.is_revoked:
        status_watermark = """
        <g transform="translate(421, 297) rotate(-25)">
          <text text-anchor="middle" y="25" font-family="Arial, sans-serif" font-size="72" font-weight="900" fill="#9E2A2B" opacity="0.22" letter-spacing="12">REVOKED</text>
        </g>
        """

    title_escaped = escape(cert.title.upper())
    recipient_escaped = escape(cert.recipient_name)
    role_escaped = escape(cert.recipient_role)
    emp_id_escaped = escape(cert.recipient.employee_id)
    desc_escaped = escape(cert.description[:180])
    signatory_name = cert.issued_by.full_name if cert.issued_by else "Executive Authority"
    signatory_escaped = escape(signatory_name)
    serial_escaped = escape(cert.serial_number)
    hash_prefix = escape(cert.verification_hash[:24])

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 842 595" width="842" height="595" font-family="Segoe UI, Roboto, Helvetica, Arial, sans-serif">
  <rect width="842" height="595" fill="#FFFBE9" />
  <defs>
    <linearGradient id="goldGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#D1C9B4" />
      <stop offset="50%" stop-color="#AD8B73" />
      <stop offset="100%" stop-color="#7F6049" />
    </linearGradient>
  </defs>

  <!-- Outer Border -->
  <rect x="20" y="20" width="802" height="555" rx="8" fill="#FFFBE9" stroke="#AD8B73" stroke-width="4" />
  <!-- Inner Thin Border -->
  <rect x="28" y="28" width="786" height="539" rx="4" fill="none" stroke="#D1C9B4" stroke-width="1.5" stroke-dasharray="8,4" />

  <!-- Corner Ornaments -->
  <circle cx="36" cy="36" r="6" fill="#AD8B73" />
  <circle cx="806" cy="36" r="6" fill="#AD8B73" />
  <circle cx="36" cy="559" r="6" fill="#AD8B73" />
  <circle cx="806" cy="559" r="6" fill="#AD8B73" />

  <!-- Organization Header -->
  <text x="421" y="85" text-anchor="middle" font-size="14" font-weight="700" letter-spacing="4" fill="#7F6049">SALOW TECHNOLOGIES</text>
  <text x="421" y="105" text-anchor="middle" font-size="11" font-weight="500" letter-spacing="1.5" fill="#6B6157">OFFICIAL RECOGNITION CREDENTIAL</text>
  <line x1="321" y1="120" x2="521" y2="120" stroke="#AD8B73" stroke-width="1.5" />

  <!-- Certificate Title -->
  <text x="421" y="170" text-anchor="middle" font-size="28" font-weight="800" letter-spacing="1" fill="#2A241F">{title_escaped}</text>

  <!-- Presentation line -->
  <text x="421" y="215" text-anchor="middle" font-size="13" font-style="italic" fill="#6B6157">This certificate is proudly presented to</text>

  <!-- Recipient Name -->
  <text x="421" y="270" text-anchor="middle" font-size="32" font-weight="700" fill="#96745C">{recipient_escaped}</text>
  <line x1="240" y1="285" x2="602" y2="285" stroke="#AD8B73" stroke-width="1" />

  <!-- Recipient Designation / Employee ID -->
  <text x="421" y="310" text-anchor="middle" font-size="12" font-weight="600" fill="#6B6157">{role_escaped} &#8226; ID: {emp_id_escaped}</text>

  <!-- Description Body -->
  <text x="421" y="360" text-anchor="middle" font-size="13" fill="#2A241F">{desc_escaped}</text>

  <!-- Footer Signatures and Verification Block -->
  <!-- Left: Issue Date -->
  <text x="90" y="475" font-size="11" font-weight="700" fill="#2A241F">Date of Issuance:</text>
  <text x="90" y="495" font-size="12" fill="#6B6157">{cert.issue_date.strftime("%B %d, %Y")}</text>

  <!-- Center: Seal -->
  <g transform="translate(421, 480)">
    <circle cx="0" cy="0" r="32" fill="url(#goldGrad)" />
    <circle cx="0" cy="0" r="28" fill="#FFFBE9" />
    <text x="0" y="4" text-anchor="middle" font-size="9" font-weight="800" fill="#7F6049">SALOWTECH</text>
    <text x="0" y="14" text-anchor="middle" font-size="7" font-weight="600" fill="#AD8B73">VERIFIED</text>
  </g>

  <!-- Right: Signatory -->
  <text x="752" y="475" text-anchor="end" font-size="11" font-weight="700" fill="#2A241F">Authorized Signatory</text>
  <text x="752" y="495" text-anchor="end" font-size="12" fill="#6B6157">{signatory_escaped}</text>

  <!-- Security Verification Bar at Bottom -->
  <rect x="40" y="525" width="762" height="25" rx="3" fill="#F6F0DC" />
  <text x="50" y="542" font-size="9" font-family="monospace" fill="#6B6157">Serial: {serial_escaped} | Verification Hash: {hash_prefix}... | Verify at /verify/{serial_escaped}</text>

  {status_watermark}
</svg>"""
    return svg
