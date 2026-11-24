import pytest

from apps.accounts.models import CustomUser
from apps.face.engine.embedder import generate_synthetic_embedding
from apps.face.engine.pipeline import (
    FaceInferencePipeline,
)
from apps.face.services import (
    enroll_face_template,
    record_biometric_acknowledgement,
)


@pytest.fixture
def employee(db):
    return CustomUser.objects.create_user(
        employee_id="ST-EMP-3001",
        full_name="Pipeline Test Employee",
        password="ValidPassword123!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.fixture
def admin(db):
    return CustomUser.objects.create_user(
        employee_id="ST-ADM-3001",
        full_name="Pipeline Admin",
        password="ValidPassword123!",
        role=CustomUser.Role.ADMIN,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.mark.django_db
class TestFaceInferencePipeline:
    def test_reject_client_side_override(self):
        """Security Invariant: Client-supplied boolean assertions are strictly rejected."""
        pipeline = FaceInferencePipeline()

        # Reject face_verified=True
        with pytest.raises(ValueError, match="Security Invariant Violation"):
            pipeline.reject_client_side_override({"face_verified": True})

        with pytest.raises(ValueError, match="Security Invariant Violation"):
            pipeline.reject_client_side_override({"is_face_verified": True})

        with pytest.raises(ValueError, match="Security Invariant Violation"):
            pipeline.reject_client_side_override({"override_liveness": True})

        # False or absent flags do not raise
        pipeline.reject_client_side_override({"face_verified": False})
        pipeline.reject_client_side_override({"other_field": "val"})

    def test_model_licenses_documented(self):
        """Pipeline must document licensing for YuNet, SFace, and MiniFASNet."""
        licenses = FaceInferencePipeline.MODEL_LICENSES
        assert "YuNet" in licenses
        assert "SFace" in licenses
        assert "MiniFASNet" in licenses
        assert "Apache-2.0" in licenses["YuNet"]
        assert "Apache-2.0" in licenses["SFace"]

    def test_1_to_1_user_mismatch_rejected(self, employee):
        """Pipeline strictly binds verification to claimed_user_id matching target_user."""
        pipeline = FaceInferencePipeline()
        result = pipeline.verify_1_to_1(
            target_user=employee,
            claimed_user_id="ST-EMP-OTHER",
            synthetic_embedding=[0.1] * 128,
        )
        assert result.success is False
        assert "does not match" in result.error_reason

    def test_unenrolled_user_fails(self, employee):
        """Target user without enrolled template fails verification."""
        pipeline = FaceInferencePipeline()
        result = pipeline.verify_1_to_1(
            target_user=employee,
            claimed_user_id=employee.employee_id,
            synthetic_embedding=[0.1] * 128,
        )
        assert result.success is False
        assert "no enrolled face template" in result.error_reason

    def test_1_to_1_matching_success_and_failure(self, employee, admin):
        """Enrolled user passes 1:1 match with matching embedding, fails on mismatch."""
        record_biometric_acknowledgement(employee, version="1.0")
        enrolled_emb = generate_synthetic_embedding(seed=555)
        matching_live = [x + 0.005 for x in enrolled_emb]
        mismatched_live = generate_synthetic_embedding(seed=999)

        enroll_face_template(
            target_user=employee,
            embedding=enrolled_emb,
            quality_score=0.96,
            enrolled_by=admin,
        )

        pipeline = FaceInferencePipeline()

        # Match
        res_match = pipeline.verify_1_to_1(
            target_user=employee,
            claimed_user_id=employee.employee_id,
            synthetic_embedding=matching_live,
        )
        assert res_match.success is True
        assert res_match.confidence_score >= 0.70

        # Mismatch
        res_mismatch = pipeline.verify_1_to_1(
            target_user=employee,
            claimed_user_id=employee.employee_id,
            synthetic_embedding=mismatched_live,
        )
        assert res_mismatch.success is False
        assert "does not match" in res_mismatch.error_reason

    def test_hardware_unavailable_reported_as_external_blocker(self, employee, admin):
        """When live frames are provided without hardware camera, reports dependency blocker."""
        record_biometric_acknowledgement(employee, version="1.0")
        enroll_face_template(
            target_user=employee,
            embedding=generate_synthetic_embedding(seed=777),
            quality_score=0.95,
            enrolled_by=admin,
        )

        pipeline = FaceInferencePipeline()
        result = pipeline.verify_1_to_1(
            target_user=employee,
            claimed_user_id=employee.employee_id,
            live_frames=[b"fake_jpeg_frame"],
        )
        assert result.success is False
        assert "BLOCKED — EXTERNAL DEPENDENCY" in result.error_reason
