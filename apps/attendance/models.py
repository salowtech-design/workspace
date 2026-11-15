from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class WorkSession(models.Model):
    """
    Work session representing an employee's attendance cycle.
    """

    class State(models.TextChoices):
        OPEN = "open", "Open"
        COMPLETED = "completed", "Completed"
        NEEDS_REVIEW = "needs_review", "Needs Review"

    class VerificationMethod(models.TextChoices):
        FACE = "face", "Face Verification"
        MANUAL_APPROVED = "manual_approved", "Manual Exception Approved"
        ADMIN_ENTRY = "admin_entry", "Administrative Entry"
        DEV_STUB = "dev_stub", "Development Stub (Dev Only)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="work_sessions",
        db_index=True,
    )
    work_date = models.DateField(
        db_index=True,
        help_text="Business date in Asia/Kolkata timezone corresponding to check-in.",
    )
    check_in_at = models.DateTimeField(db_index=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    verification_method = models.CharField(
        max_length=32,
        choices=VerificationMethod.choices,
        default=VerificationMethod.DEV_STUB,
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.OPEN)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-check_in_at"]
        verbose_name = "Work Session"
        verbose_name_plural = "Work Sessions"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(check_out_at__isnull=True)
                | models.Q(check_out_at__gte=models.F("check_in_at")),
                name="check_out_after_check_in",
            ),
        ]

    def __str__(self):
        return f"{self.user.employee_id} - {self.work_date} [{self.state}]"

    def clean(self):
        super().clean()
        if self.check_out_at and self.check_in_at and self.check_out_at < self.check_in_at:
            raise ValidationError("Check-out time must be after check-in time.")
