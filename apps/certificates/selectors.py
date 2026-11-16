from typing import Optional

from django.db.models import Q, QuerySet

from apps.accounts.models import CustomUser
from apps.certificates.models import Certificate, CertificateTemplate


def get_certificate_by_identifier(identifier: str) -> Optional[Certificate]:
    """
    Find certificate by serial number, UUID string, or SHA-256 verification hash.
    Used for public verification and internal lookups.
    """
    clean_id = identifier.strip()
    return (
        Certificate.objects.select_related("recipient", "issued_by", "template")
        .filter(
            Q(serial_number__iexact=clean_id)
            | Q(uuid__iexact=clean_id)
            | Q(verification_hash__iexact=clean_id)
        )
        .first()
    )


def list_certificates_for_recipient(user: CustomUser) -> QuerySet[Certificate]:
    """Retrieve all certificates issued to an individual user."""
    return (
        Certificate.objects.select_related("issued_by", "template")
        .filter(recipient=user)
        .order_by("-issue_date")
    )


def list_all_certificates(
    *,
    search: Optional[str] = None,
    status: Optional[str] = None,
) -> QuerySet[Certificate]:
    """Retrieve all certificates with optional filtering for management portal."""
    qs = Certificate.objects.select_related("recipient", "issued_by", "template").all()

    if status == "revoked":
        qs = qs.filter(is_revoked=True)
    elif status == "active":
        qs = qs.filter(is_revoked=False)

    if search:
        term = search.strip()
        qs = qs.filter(
            Q(serial_number__icontains=term)
            | Q(title__icontains=term)
            | Q(recipient_name__icontains=term)
            | Q(recipient__employee_id__icontains=term)
        )

    return qs.order_by("-issue_date", "-created_at")


def list_active_certificate_templates() -> QuerySet[CertificateTemplate]:
    """List active certificate templates for issuance dropdowns."""
    return CertificateTemplate.objects.filter(is_active=True).order_by("title")
