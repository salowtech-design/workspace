import uuid
from typing import Optional

from django.db.models import Q, QuerySet
from django.db.models.functions import Upper

from apps.accounts.models import CustomUser
from apps.certificates.models import Certificate, CertificateTemplate


_AMBIGUOUS = object()


def _case_folded_unique(field: str, value: str):
    """One field: the certificate, None when absent, or _AMBIGUOUS.

    An exact match wins. Otherwise a case-blind match counts only when it is the single candidate.
    TiDB's binary-collation unique index does not stop two values that differ only by letter case. The app
    never creates them (serials are upper case, hashes lower-case hex), but rows written around the app
    could, and the guard does not rely on that format.
    """
    base = Certificate.objects.select_related("recipient", "issued_by", "template")
    exact = base.filter(**{field: value}).first()
    if exact:
        return exact
    folded = list(base.annotate(_fold=Upper(field)).filter(_fold=value.upper())[:2])
    if len(folded) > 1:
        return _AMBIGUOUS
    return folded[0] if folded else None


def get_certificate_by_serial_uuid_or_hash(identifier: str) -> Optional[Certificate]:
    """Find by serial, UUID (any letter case) or verification hash. Shared by the lookup and public verify.

    Ambiguity ends the lookup with no certificate, which is different from absent: two certificates that
    match one field only by case, or one value that matches different certificates in different fields,
    never fall through to another field or pick one arbitrarily.
    """
    clean_id = identifier.strip()
    found = []
    for field in ("serial_number", "verification_hash"):
        hit = _case_folded_unique(field, clean_id)
        if hit is _AMBIGUOUS:
            return None
        if hit:
            found.append(hit)
    try:
        by_uuid = (
            Certificate.objects.select_related("recipient", "issued_by", "template")
            .filter(uuid=uuid.UUID(clean_id))
            .first()
        )
    except ValueError:
        by_uuid = None
    if by_uuid:
        found.append(by_uuid)
    if len({c.pk for c in found}) > 1:
        return None
    return found[0] if found else None


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
