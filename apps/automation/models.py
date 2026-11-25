import uuid

from django.conf import settings
from django.db import models


class WfDefinition(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=1)
    definition = models.JSONField(default=dict)
    enabled = models.BooleanField(default=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wf_definitions"
    )
    created_at = models.DateTimeField(auto_now_add=True)


class WfRun(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"

    definition = models.ForeignKey(WfDefinition, on_delete=models.CASCADE, related_name="runs")
    version = models.PositiveIntegerField()  # definition version this run was started from
    definition_snapshot = models.JSONField(default=dict)
    trigger_key = models.CharField(max_length=64)  # event id, or a fresh id for a manual start
    state = models.CharField(max_length=12, choices=State.choices, default=State.QUEUED)
    next_step = models.PositiveIntegerField(default=0)
    attempt = models.PositiveIntegerField(default=0)  # fencing token, bumped on every claim
    lease_until = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["definition", "trigger_key"], name="wf_run_one_per_trigger"
            )
        ]
        indexes = [models.Index(fields=["state", "lease_until"])]


class WfStepResult(models.Model):
    run = models.ForeignKey(WfRun, on_delete=models.CASCADE, related_name="steps")
    step_index = models.PositiveIntegerField()
    state = models.CharField(max_length=8)  # ok, skipped
    output = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=40, unique=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "step_index"], name="wf_step_once_per_run")
        ]
