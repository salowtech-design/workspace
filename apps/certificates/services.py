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
    from apps.certificates.selectors import get_certificate_by_serial_uuid_or_hash

    cert = get_certificate_by_serial_uuid_or_hash(clean_id)

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


def _emblem_data_uri() -> str:
    """The Salowtech emblem as a data URI, so an exported SVG is self-contained."""
    import base64
    from pathlib import Path

    from django.conf import settings

    path = Path(settings.BASE_DIR) / "static" / "img" / "salowtech-emblem.png"
    try:
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return ""


def _qr_data_uri(url: str) -> str:
    """QR code for the public verification page, as an SVG data URI."""
    import base64

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1)
    return "data:image/svg+xml;base64," + base64.b64encode(img.to_string()).decode()


def generate_certificate_svg(cert: Certificate, base_url: str = "") -> str:  # noqa: C901
    """
    Print-ready vector SVG in the same design as the on-screen certificate: Salowtech emblem
    (embedded), brand name, recipient, QR code to the public verification page, signatory,
    serial and hash, and a REVOKED stamp when revoked. A4 landscape (842 x 595). Dynamic text
    is XML-escaped.
    """
    import textwrap

    emblem = _emblem_data_uri()
    from django.urls import reverse

    verify_path = reverse("public_verify_id", args=[cert.serial_number])
    qr = _qr_data_uri((base_url.rstrip("/") + verify_path) if base_url else verify_path)
    revoked = ""
    if cert.is_revoked:
        when = cert.revoked_at.strftime("%B %d, %Y").upper() if cert.revoked_at else ""
        revoked = (
            '<g transform="translate(715, 78) rotate(4)">'
            '<rect x="-75" y="-22" width="150" height="40" fill="none" stroke="#A12828" '
            'stroke-width="2"/>'
            '<text text-anchor="middle" y="-5" font-size="14" font-weight="800" '
            'letter-spacing="3" fill="#A12828">REVOKED</text>'
            f'<text text-anchor="middle" y="10" font-size="9" letter-spacing="1" '
            f'fill="#A12828">{escape(when)}</text></g>'
            '<g transform="translate(421, 330) rotate(-22)"><text text-anchor="middle" '
            'font-size="84" font-weight="900" letter-spacing="10" fill="#A12828" '
            'opacity="0.16">REVOKED</text></g>'
        )
    desc_lines = textwrap.wrap(cert.description or "", width=95)[:3]
    desc = "".join(
        f'<text x="421" y="{352 + i * 18}" text-anchor="middle" font-size="12" fill="#4A4038">'
        f"{escape(line)}</text>"
        for i, line in enumerate(desc_lines)
    )
    signatory = escape(cert.issued_by.full_name if cert.issued_by else "Executive Authority")
    serial = escape(cert.serial_number)
    emblem_img = f'<image href="{emblem}" x="391" y="40" width="60" height="60"/>' if emblem else ""
    watermark = (
        f'<image href="{emblem}" x="271" y="147" width="300" height="300" opacity="0.06"/>'
        if emblem
        else ""
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 842 595" width="842" height="595" font-family="Segoe UI, Roboto, Helvetica, Arial, sans-serif">
  <rect width="842" height="595" fill="#FFFBE9"/>
  <rect x="20" y="20" width="802" height="555" rx="8" fill="#FFFBE9" stroke="#AD8B73" stroke-width="4"/>
  <rect x="28" y="28" width="786" height="539" rx="4" fill="none" stroke="#C9A584" stroke-width="1.5" stroke-dasharray="8,4"/>
  {watermark}
  {emblem_img}
  <text x="421" y="126" text-anchor="middle" font-size="18" font-weight="800" letter-spacing="3" fill="#2A241F">SALOWTECH</text>
  <text x="421" y="143" text-anchor="middle" font-size="9" letter-spacing="3" fill="#7A6652">OFFICIAL RECOGNITION CREDENTIAL</text>
  <text x="421" y="188" text-anchor="middle" font-size="30" font-weight="700" fill="#2A241F">Certificate of Recognition</text>
  <line x1="341" y1="202" x2="501" y2="202" stroke="#AD8B73" stroke-width="1.5"/>
  <text x="421" y="228" text-anchor="middle" font-size="12" font-style="italic" fill="#5B4F44">This is to certify that</text>
  <text x="421" y="268" text-anchor="middle" font-size="30" font-weight="700" fill="#2A241F">{escape(cert.recipient_name)}</text>
  <line x1="231" y1="280" x2="611" y2="280" stroke="#2A241F" stroke-width="1.2"/>
  <text x="421" y="302" text-anchor="middle" font-size="12" fill="#5B4F44">{escape(cert.recipient_role)} &#8226; Employee ID: {escape(cert.recipient.employee_id)}</text>
  <text x="421" y="334" text-anchor="middle" font-size="18" font-weight="700" fill="#2A241F">{escape(cert.title)}</text>
  {desc}
  <text x="110" y="470" font-size="12" font-weight="700" fill="#2A241F">{cert.issue_date.strftime("%B %d, %Y")}</text>
  <line x1="90" y1="476" x2="250" y2="476" stroke="#2A241F" stroke-width="0.8"/>
  <text x="110" y="491" font-size="10" fill="#5B4F44">Date of Issuance</text>
  <image href="{qr}" x="381" y="420" width="80" height="80"/>
  <text x="732" y="470" text-anchor="end" font-size="12" font-weight="700" fill="#2A241F">{signatory}</text>
  <line x1="592" y1="476" x2="752" y2="476" stroke="#2A241F" stroke-width="0.8"/>
  <text x="732" y="491" text-anchor="end" font-size="10" fill="#5B4F44">Authorized Signatory</text>
  <rect x="40" y="522" width="762" height="30" rx="3" fill="#F6F0DC"/>
  <text x="50" y="535" font-size="9" font-family="monospace" fill="#5B4F44">Serial: {serial} | Hash: {escape(cert.verification_hash[:24])}...</text>
  <text x="50" y="547" font-size="9" font-family="monospace" fill="#5B4F44">To verify, scan the QR code or open {escape(verify_path)}</text>
  {revoked}
</svg>"""
