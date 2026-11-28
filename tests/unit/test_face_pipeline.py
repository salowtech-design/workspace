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
        assert all("unreviewed" in v or "unresolved" in v for v in licenses.values()), licenses
        assert "Apache-2.0 repository licence" in licenses["SFace"]

    def test_1_to_1_user_mismatch_rejected(self, employee):
        """Pipeline strictly binds verification to claimed_user_id matching target_user."""
        pipeline = FaceInferencePipeline()
        result = pipeline.verify_1_to_1(
            target_user=employee,
            claimed_user_id="ST-EMP-OTHER",
        )
        assert result.success is False
        assert "does not match" in result.error_reason

    def test_unenrolled_user_fails(self, employee):
        """Target user without enrolled template fails verification."""
        pipeline = FaceInferencePipeline()
        result = pipeline.verify_1_to_1(
            target_user=employee,
            claimed_user_id=employee.employee_id,
        )
        assert result.success is False
        assert "no enrolled face template" in result.error_reason

    def test_decision_logic_with_supplied_measurements_adapter_logic_only(self, employee, admin):
        """ADAPTER LOGIC ONLY, not working face matching or liveness: the decision gate, given an enrolled
        (decrypted) vector and a supplied vector with explicit measurements, compares them with the unmeasured
        0.70 constant. verify_1_to_1 has no such path."""
        from apps.face.engine.crypto import decrypt_template
        from apps.face.engine.verifier import ProductionFaceVerifier
        from apps.face.models import FaceTemplate
        from apps.face.services import get_face_key_ring

        record_biometric_acknowledgement(employee, version="1.0")
        enrolled_emb = generate_synthetic_embedding(seed=555)
        enroll_face_template(target_user=employee, embedding=enrolled_emb, quality_score=0.96, enrolled_by=admin)
        stored = decrypt_template(FaceTemplate.objects.get(user=employee).encrypted_template, get_face_key_ring())
        gate = ProductionFaceVerifier()
        common = dict(liveness_score=0.92, brightness=100.0, sharpness=85.0, face_width=120, face_height=120)
        assert gate.verify_attempt(enrolled_embedding=stored, live_embedding=[x + 0.005 for x in enrolled_emb], **common).success
        res = gate.verify_attempt(enrolled_embedding=stored, live_embedding=generate_synthetic_embedding(seed=999), **common)
        assert res.success is False and "does not match" in res.error_reason

    def test_verify_1_to_1_has_no_supplied_embedding_path_and_always_fails_closed(self, employee, admin, monkeypatch):
        """verify_1_to_1 cannot succeed: no embedding parameter, no invented quality or liveness values, and it still
        fails closed when the camera and runtime flags are forced True, with or without frames."""
        import inspect

        from apps.face.engine import pipeline as pl

        record_biometric_acknowledgement(employee, version="1.0")
        emb = generate_synthetic_embedding(seed=555)
        enroll_face_template(target_user=employee, embedding=emb, quality_score=0.96, enrolled_by=admin)
        params = inspect.signature(FaceInferencePipeline.verify_1_to_1).parameters
        assert "synthetic_embedding" not in params and "live_embedding" not in params
        with pytest.raises(TypeError):
            FaceInferencePipeline().verify_1_to_1(target_user=employee, claimed_user_id=employee.employee_id,
                                                  synthetic_embedding=emb)  # fmt: skip
        no_hw = "Physical camera sensor hardware and ONNX Runtime CPU inference engine are not available"
        no_cam = "Physical camera hardware sensor not attached."
        for forced in (False, True):
            monkeypatch.setattr(pl, "CAMERA_HARDWARE_AVAILABLE", forced)
            monkeypatch.setattr(pl, "ONNX_RUNTIME_AVAILABLE", forced)
            pipe = FaceInferencePipeline()
            assert pipe.is_hardware_ready() is forced, "the forced flags did not take effect"
            for frames in (None, [b"frame"]):
                res = pipe.verify_1_to_1(target_user=employee, claimed_user_id=employee.employee_id, live_frames=frames)
                expected = no_hw if (frames and not forced) else no_cam
                assert res.success is False and res.confidence_score == 0.0 and res.liveness_score == 0.0
                assert expected in res.error_reason, (forced, frames, res.error_reason)
                assert "enrolled" not in res.error_reason and "does not match" not in res.error_reason

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
