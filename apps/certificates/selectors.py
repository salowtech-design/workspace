import uuid
from typing import Optional

from django.db.models import Q, QuerySet
from django.db.models.functions import Upper

from apps.accounts.models import CustomUser
from apps.certificates.models import Certificate, CertificateTemplate


def _case_folded_unique(field: str, value: str) -> Optional[Certificate]:
    """An exact match wins. Otherwise a case-blind match is used only when it is the single candidate.

    The unique indexes on TiDB's binary collation do not stop two values that differ only by letter case
    (the app never creates them: serials are upper case, hashes lower-case hex). If rows written around the
    app ever collide that way, no certificate is picked arbitrarily: the lookup finds nothing.
    """
    base = Certificate.objects.select_related("recipient", "issued_by", "template")
    exact = base.filter(**{field: value}).first()
    if exact:
        return exact
    folded = list(base.annotate(_fold=Upper(field)).filter(_fold=value.upper())[:2])
    return folded[0] if len(folded) == 1 else None


def get_certificate_by_serial_uuid_or_hash(identifier: str) -> Optional[Certificate]:
    """Serial, then UUID (any letter case), then verification hash. Shared by the lookup and public verify."""
    clean_id = identifier.strip()
    cert = _case_folded_unique("serial_number", clean_id)
    if cert:
        return cert
    try:
        cert = (
            Certificate.objects.select_related("recipient", "issued_by", "template")
            .filter(uuid=uuid.UUID(clean_id))
            .first()
        )
    except ValueError:
        cert = None
    return cert or _case_folded_unique("verification_hash", clean_id)


def get_certificate_by_identifier(identifier: str) -> Optional[Certificate]:
    """
    Find certificate by serial number, UUID string, or SHA-256 verification hash.
    Used for public verification and internal lookups.
    """
    return get_certificate_by_serial_uuid_or_hash(identifier)


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
        # Upper() on both sides: icontains is case-sensitive on TiDB's binary collation
        needle = term.upper()
        qs = qs.annotate(
            _u_serial=Upper("serial_number"),
            _u_title=Upper("title"),
            _u_name=Upper("recipient_name"),
            _u_emp=Upper("recipient__employee_id"),
        ).filter(
            Q(_u_serial__contains=needle)
            | Q(_u_title__contains=needle)
            | Q(_u_name__contains=needle)
            | Q(_u_emp__contains=needle)
        )

    return qs.order_by("-issue_date", "-created_at")


def list_active_certificate_templates() -> QuerySet[CertificateTemplate]:
    """List active certificate templates for issuance dropdowns."""
    return CertificateTemplate.objects.filter(is_active=True).order_by("title")
