from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.core.services import record_audit_event
from apps.face.engine.crypto import (
    BiometricKeyRing,
    decrypt_template,
    encrypt_template,
)
from apps.face.models import FaceEnrollment


def enroll_user_face(
    target_user: CustomUser,
    embedding: list[float],
    quality_score: float,
    enrolled_by: CustomUser,
    key_ring: BiometricKeyRing,
    notice_acknowledged_at=None,
) -> FaceEnrollment:
    """
    Encrypts and persists biometric face representation.
    Enforces privacy by storing ONLY AES-256-GCM ciphertext.
    """
    if enrolled_by.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can enroll employee biometric templates.")

    if len(embedding) != 128:
        raise ValidationError(f"Invalid embedding dimension ({len(embedding)}; 128 required).")

    if quality_score < 0.60:
        raise ValidationError("Face quality score is too low for reliable biometric enrollment.")

    ciphertext = encrypt_template(embedding, key_ring)
    ack_time = notice_acknowledged_at or timezone.now()

    enrollment, _ = FaceEnrollment.objects.update_or_create(
        user=target_user,
        defaults={
            "encrypted_template": ciphertext,
            "key_id": key_ring.active_key_id,
            "quality_score": quality_score,
            "notice_acknowledged_at": ack_time,
            "enrolled_by": enrolled_by,
            "is_active": True,
            "revoked_at": None,
        },
    )

    record_audit_event(
        event_type="biometrics.face_enrolled",
        actor=enrolled_by,
        target_entity="FaceEnrollment",
        target_id=target_user.employee_id,
        details={
            "key_id": key_ring.active_key_id,
            "quality_score": quality_score,
            "user_id": target_user.employee_id,
        },
    )
    return enrollment


def revoke_user_face(target_user: CustomUser, reason: str, actor: CustomUser) -> None:
    """Revoke biometric enrollment."""
    if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can revoke biometric templates.")

    enrollment = getattr(target_user, "face_enrollment", None)
    if not enrollment or not enrollment.is_active:
        raise ValidationError("No active face enrollment found for this user.")

    enrollment.is_active = False
    enrollment.revoked_at = timezone.now()
    enrollment.save(update_fields=["is_active", "revoked_at"])

    record_audit_event(
        event_type="biometrics.face_revoked",
        actor=actor,
        target_entity="FaceEnrollment",
        target_id=target_user.employee_id,
        details={"reason": reason},
    )


def get_user_embedding(target_user: CustomUser, key_ring: BiometricKeyRing) -> list[float]:
    """Retrieve and decrypt template for attendance verification."""
    enrollment = getattr(target_user, "face_enrollment", None)
    if not enrollment or not enrollment.is_active:
        raise ValidationError(f"User {target_user.employee_id} has no active face enrollment.")

    return decrypt_template(bytes(enrollment.encrypted_template), key_ring)
