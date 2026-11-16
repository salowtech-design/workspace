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


class OutboxEvent(models.Model):
    """
    Transactional outbox event store.
    Guarantees reliable, at-least-once domain event publication.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"

    event_id = models.CharField(max_length=64, primary_key=True)
    event_type = models.CharField(max_length=128, db_index=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    retry_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Outbox Event"
        verbose_name_plural = "Outbox Events"

    def __str__(self):
        return f"Event {self.event_type} [{self.event_id[:8]}] - {self.status}"


class WorkspaceSetting(models.Model):
    """Dynamic, schema-validated configuration parameters."""

    key = models.CharField(max_length=64, unique=True, db_index=True)
    value = models.JSONField(default=dict)
    schema = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_settings",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        verbose_name = "Workspace Setting"
        verbose_name_plural = "Workspace Settings"

    def __str__(self):
        return f"Setting: {self.key}"


class FeatureFlag(models.Model):
    """Dynamic feature flag control."""

    name = models.CharField(max_length=64, unique=True, db_index=True)
    is_enabled = models.BooleanField(default=False)
    allowed_roles = models.JSONField(default=list, blank=True)
    description = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Feature Flag"
        verbose_name_plural = "Feature Flags"

    def __str__(self):
        status = "ON" if self.is_enabled else "OFF"
        return f"Flag {self.name} [{status}]"


class RateLimitRecord(models.Model):
    """
    Centralized, distributed-safe sliding window rate limit log.
    Ensures abuse protection is coordinated across all worker processes and nodes via TiDB.
    """

    key = models.CharField(max_length=128, db_index=True)
    timestamp = models.FloatField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Rate Limit Record"
        verbose_name_plural = "Rate Limit Records"
        indexes = [
            models.Index(fields=["key", "timestamp"]),
        ]

    def __str__(self):
        return f"RateLimitRecord for {self.key} at {self.timestamp}"
