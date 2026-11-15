from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models


class AuditLog(models.Model):
    """
    Immutable, append-only security and business audit log.
    Captures actor attribution, action codes, and redacted summaries.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64)
    target_id = models.CharField(max_length=64)
    payload_summary = models.JSONField(default=dict)
    request_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"

    def __str__(self):
        actor_str = getattr(self.actor, "employee_id", "system")
        return (
            f"[{self.created_at}] {actor_str} - {self.action} on "
            f"{self.target_type}:{self.target_id}"
        )

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionDenied("AuditLog records are strictly immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("AuditLog records cannot be deleted.")
