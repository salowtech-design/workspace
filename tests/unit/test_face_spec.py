"""
Unit Tests for Face Recognition System (FRS) Specification.
Verifies informed consent notice requirements, admin-assisted enrollment,
AES-256-GCM template encryption, 1:1 verification, and fallback handling.
"""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import CustomUser
from apps.face.engine.crypto import decrypt_template
from apps.face.models import (
    FaceVerificationLog,
)
from apps.face.services import (
    enroll_face_template,
    get_face_key_ring,
    record_biometric_acknowledgement,
    verify_face,
)


@pytest.fixture
def employee(db):
    return CustomUser.objects.create_user(
        employee_id="ST-EMP-2001",
        full_name="Bob Biometric",
        password="ValidStrongPassword123!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.fixture
def admin(db):
    return CustomUser.objects.create_user(
        employee_id="ST-ADM-2001",
        full_name="Admin Alice",
        password="ValidStrongPassword123!",
        role=CustomUser.Role.ADMIN,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.mark.django_db
class TestBiometricNoticeAndEnrollment:
    def test_enrollment_fails_without_consent_notice(self, employee, admin):
        """Enrollment cannot proceed without prior written/digital consent acknowledgment."""
        embedding = [0.1] * 128
        with pytest.raises(ValidationError, match="Biometric notice must be acknowledged"):
            enroll_face_template(
                target_user=employee,
                embedding=embedding,
                quality_score=0.95,
                enrolled_by=admin,
            )

    def test_enrollment_must_be_admin_assisted(self, employee):
        """Standard employees cannot enroll their own or other templates."""
        record_biometric_acknowledgement(employee, version="1.0")
        embedding = [0.1] * 128
        with pytest.raises(PermissionDenied, match="admin-assisted"):
            enroll_face_template(
                target_user=employee,
                embedding=embedding,
                quality_score=0.95,
                enrolled_by=employee,
            )

    def test_enrollment_validates_128d_embedding_vector(self, employee, admin):
        """Embedding vector must be exactly 128 float values."""
        record_biometric_acknowledgement(employee, version="1.0")
        invalid_embedding = [0.1] * 64
        with pytest.raises(ValidationError, match="exactly 128 dimensions"):
            enroll_face_template(
                target_user=employee,
                embedding=invalid_embedding,
                quality_score=0.95,
                enrolled_by=admin,
            )

    def test_enrollment_aes256_encryption_at_rest(self, employee, admin):
        """Templates are encrypted with AES-256-GCM; raw floats are never stored."""
        record_biometric_acknowledgement(employee, version="1.0")
        sample_embedding = [float(i) / 128.0 for i in range(128)]

        template = enroll_face_template(
            target_user=employee,
            embedding=sample_embedding,
            quality_score=0.98,
            enrolled_by=admin,
        )

        assert template.user == employee
        assert template.key_id == "k1"
        assert template.algorithm == "SFace-ONNX"

        # Verify decryption recovers exact embedding floats
        kr = get_face_key_ring()
        decrypted = decrypt_template(template.encrypted_template, kr)
        for original, recovered in zip(sample_embedding, decrypted, strict=True):
            assert abs(original - recovered) < 1e-5


@pytest.mark.django_db
class TestFaceVerificationService:
    def test_verify_face_unenrolled_user_fails_with_fallback_notice(self, employee):
        """Unenrolled employee verification fails gracefully and logs attempt."""
        result = verify_face(
            user=employee,
            purpose=FaceVerificationLog.Purpose.ATTENDANCE_CHECK_IN,
        )
        assert result.success is False
        assert "Manual fallback available" in result.error_reason

        # Verification log must be recorded
        log = FaceVerificationLog.objects.filter(user=employee).first()
        assert log is not None
        assert log.decision == FaceVerificationLog.Decision.FAILURE
        assert log.purpose == FaceVerificationLog.Purpose.ATTENDANCE_CHECK_IN

    def test_verify_face_enrolled_user_attendance_success(self, employee, admin):
        """Enrolled employee passes attendance verification."""
        record_biometric_acknowledgement(employee, version="1.0")
        sample_embedding = [0.05] * 128
        enroll_face_template(
            target_user=employee,
            embedding=sample_embedding,
            quality_score=0.92,
            enrolled_by=admin,
        )

        result = verify_face(
            user=employee,
            purpose=FaceVerificationLog.Purpose.ATTENDANCE_CHECK_IN,
        )
        assert result.success is True

        # Check log
        log = FaceVerificationLog.objects.filter(user=employee).first()
        assert log is not None
        assert log.decision == FaceVerificationLog.Decision.SUCCESS

    def test_step_up_face_verification_disabled_by_default(self, employee, admin):
        """Step-up face elevation is disabled by default; requires authenticator fallback."""
        record_biometric_acknowledgement(employee, version="1.0")
        enroll_face_template(
            target_user=employee,
            embedding=[0.05] * 128,
            quality_score=0.92,
            enrolled_by=admin,
        )

        result = verify_face(
            user=employee,
            purpose=FaceVerificationLog.Purpose.STEP_UP,
        )
        assert result.success is False
        assert "Face step-up is currently disabled" in result.error_reason

    def test_unsupported_purpose_raises_error(self, employee):
        """Invalid purpose string is rejected."""
        with pytest.raises(ValueError, match="Unsupported face verification purpose"):
            verify_face(user=employee, purpose="unauthorized_purpose")
