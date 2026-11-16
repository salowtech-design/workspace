from django.contrib import admin

from apps.certificates.models import Certificate, CertificateTemplate, CertificateVerificationLog


@admin.register(CertificateTemplate)
class CertificateTemplateAdmin(admin.ModelAdmin):
    list_display = ("title", "code", "is_active", "created_at")
    search_fields = ("title", "code")


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = (
        "serial_number",
        "recipient_name",
        "title",
        "issue_date",
        "is_revoked",
        "issued_by",
    )
    list_filter = ("is_revoked", "issue_date", "template")
    search_fields = (
        "serial_number",
        "recipient_name",
        "recipient__employee_id",
        "verification_hash",
    )
    readonly_fields = ("serial_number", "uuid", "verification_hash", "created_at", "updated_at")


@admin.register(CertificateVerificationLog)
class CertificateVerificationLogAdmin(admin.ModelAdmin):
    list_display = ("certificate", "ip_address", "verified_at")
    list_filter = ("verified_at",)
    search_fields = ("certificate__serial_number", "ip_address")
