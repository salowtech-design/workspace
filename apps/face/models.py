from django.conf import settings
from django.db import models


class BiometricNoticeAcknowledgement(models.Model):
    """Immutable audit record of employee informed consent for biometric processing."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="biometric_acknowledgements",
    )
    notice_version = models.CharField(max_length=16, default="1.0")
    acknowledged_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-acknowledged_at"]
        verbose_name = "Biometric Notice Acknowledgement"
        verbose_name_plural = "Biometric Notice Acknowledgements"

    def __str__(self):
        return (
            f"Consent v{self.notice_version} by {self.user.employee_id} at {self.acknowledged_at}"
        )


class FaceTemplate(models.Model):
    """
    AES-256-GCM encrypted biometric template.
    Raw images, video frames, or unencrypted embeddings are STRICTLY NEVER PERSISTED.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="face_template",
    )
    encrypted_template = models.BinaryField(
        help_text="AES-256-GCM encrypted 128-dimensional float32 feature embedding."
    )
    nonce = models.BinaryField(help_text="96-bit cryptographic nonce.")
    key_id = models.CharField(max_length=32, default="k1")
    algorithm = models.CharField(max_length=32, default="SFace-ONNX")
    quality_score = models.FloatField(default=0.0)
    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrolled_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Face Template"
        verbose_name_plural = "Face Templates"

    def __str__(self):
        return f"FaceTemplate for {self.user.employee_id} [{self.algorithm}]"


class FaceVerificationLog(models.Model):
    """
    Audit log of 1:1 biometric verification attempts.
    Records decisions, scores, and purpose. Never stores image payloads.
    """

    class Purpose(models.TextChoices):
        ATTENDANCE_CHECK_IN = "attendance_check_in", "Attendance Check-In"
        ATTENDANCE_CHECK_OUT = "attendance_check_out", "Attendance Check-Out"
        ENROLLMENT = "enrollment", "Enrollment"
        STEP_UP = "step_up", "Step-Up Elevation"

    class Decision(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Verification Failure"
        SPOOF_DETECTED = "spoof_detected", "Presentation Attack Detected"
        LOW_QUALITY = "low_quality", "Low Image Quality"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="face_verification_logs",
    )
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    decision = models.CharField(max_length=32, choices=Decision.choices)
    similarity_score = models.FloatField(default=0.0)
    threshold_applied = models.FloatField(default=0.70)
    duration_ms = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Face Verification Log"
        verbose_name_plural = "Face Verification Logs"

    def __str__(self):
        return f"{self.user.employee_id} - {self.purpose} [{self.decision}] at {self.created_at}"
