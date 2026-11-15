from django.conf import settings
from django.db import models


class FaceEnrollment(models.Model):
    """
    Stores encrypted biometric templates for face verification.
    STRICT PRIVACY GUARANTEE: Raw images and video frames are NEVER persisted.
    Only AES-256-GCM ciphertexts of 128-d embeddings are stored.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="face_enrollment",
    )
    encrypted_template = models.BinaryField(
        help_text="AES-256-GCM encrypted 128-dim float32 face embedding."
    )
    key_id = models.CharField(max_length=32, db_index=True)
    algorithm = models.CharField(max_length=64, default="YuNet-SFace-128d")
    quality_score = models.FloatField(default=1.0)
    notice_acknowledged_at = models.DateTimeField(
        help_text="Timestamp when employee acknowledged the biometric consent notice."
    )
    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrolled_users",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Face Enrollment"
        verbose_name_plural = "Face Enrollments"

    def __str__(self):
        status = "Active" if self.is_active else "Revoked"
        return f"FaceEnrollment [{status}] for {self.user.employee_id}"
