import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class CertificateTemplate(models.Model):
    """Certificate template preset (e.g. Internship, Excellence, Service)."""

    code = models.CharField(max_length=64, unique=True, db_index=True)
    title = models.CharField(max_length=200)
    description_template = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]
        verbose_name = "Certificate Template"
        verbose_name_plural = "Certificate Templates"

    def __str__(self):
        return f"{self.title} [{self.code}]"


class Certificate(models.Model):
    """
    Issued official credential with cryptographic tamper-evident SHA-256 verification hash.
    Can be publicly verified at /verify/<serial_or_uuid>.
    """

    serial_number = models.CharField(max_length=64, unique=True, db_index=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="certificates",
        db_index=True,
    )
    template = models.ForeignKey(
        CertificateTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_instances",
    )
    title = models.CharField(max_length=200)
    recipient_name = models.CharField(max_length=128)
    recipient_role = models.CharField(max_length=64, blank=True)
    description = models.TextField()
    issue_date = models.DateField(default=timezone.now, db_index=True)
    expiry_date = models.DateField(null=True, blank=True)

    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="issued_certificates",
    )

    # Cryptographic integrity signature
    verification_hash = models.CharField(max_length=64, unique=True, db_index=True)

    # Revocation tracking
    is_revoked = models.BooleanField(default=False, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_certificates",
    )
    revocation_reason = models.TextField(blank=True)
    # Linked reissue: the replacement points at the revoked certificate it supersedes.
    supersedes = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reissued_as",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issue_date", "-created_at"]
        verbose_name = "Certificate"
        verbose_name_plural = "Certificates"

    def __str__(self):
        return f"{self.serial_number} — {self.recipient_name} ({self.title})"


class CertificateVerificationLog(models.Model):
    """Audit log tracking public verification requests and lookups."""

    certificate = models.ForeignKey(
        Certificate,
        on_delete=models.CASCADE,
        related_name="verification_logs",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    verified_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-verified_at"]
        verbose_name = "Certificate Verification Log"
        verbose_name_plural = "Certificate Verification Logs"

    def __str__(self):
        return f"Verify {self.certificate.serial_number} at {self.verified_at}"
