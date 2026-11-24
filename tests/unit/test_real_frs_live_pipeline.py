"""
Automated End-to-End Acceptance Tests for Live Face Recognition System (FRS).
Verifies:
1. Real YuNet Face Detection & 5-point landmark extraction.
2. Real SFace 128-dimensional embedding extraction and L2 normalization.
3. Real MiniFASNet passive presentation attack detection (PAD).
4. Real 1:1 Biometric Matching with cosine similarity comparison.
5. Admin-assisted live enrollment from device camera frames with AES-256-GCM encryption.
6. Server-side live verification endpoint with real metrics (similarity, duration_ms, liveness).
7. Strict rejection of client-supplied boolean assertions (face_verified=True).
8. Server-issued active motion challenge flow.
"""

import base64
import os
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.face.engine.crypto import decrypt_template
from apps.face.engine.embedder import compute_cosine_similarity
from apps.face.engine.pipeline import FaceInferencePipeline
from apps.face.models import (
    BiometricNoticeAcknowledgement,
    FaceTemplate,
    FaceVerificationLog,
)
from apps.face.services import (
    enroll_face_from_camera_frame,
    get_face_key_ring,
    record_biometric_acknowledgement,
    verify_face,
)

User = get_user_model()


@pytest.fixture
def sample_face_bytes():
    """Load real human face test fixture."""
    path = os.path.join(os.getcwd(), "tests", "fixtures", "sample_face.jpg")
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def sample_face_base64(sample_face_bytes):
    """Base64 data URL string representing camera capture."""
    b64 = base64.b64encode(sample_face_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


@pytest.fixture
def test_employee(db):
    return User.objects.create_user(
        employee_id="ST-FRS-EMP-01",
        email="frs.employee@salowtech.com",
        full_name="FRS Test Employee",
        password="ValidStrongPassword123!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.fixture
def test_admin(db):
    return User.objects.create_user(
        employee_id="ST-FRS-ADM-01",
        email="frs.admin@salowtech.com",
        full_name="FRS Biometric Admin",
        password="ValidStrongPassword123!",
        role=CustomUser.Role.ADMIN,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.mark.django_db
class TestRealFRSInferencePipeline:
    def test_models_exist_and_status_endpoint(self):
        """Verify all three ONNX models exist and status endpoint reports ready."""
        pipeline = FaceInferencePipeline()
        assert pipeline.models_available is True
        assert os.path.exists(pipeline.yunet_model_path)
        assert os.path.exists(pipeline.sface_model_path)
        assert os.path.exists(pipeline.minifasnet_model_path)

        client = Client()
        res = client.get(reverse("face:status"))
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ready"
        assert data["models_loaded"] is True
        assert data["security_invariants"]["zero_raw_image_retention"] is True

    def test_real_yunet_detection_and_sface_feature_extraction(self, sample_face_bytes):
        """Verify real YuNet detects face and SFace extracts 128d unit-normalized embedding."""
        pipeline = FaceInferencePipeline()
        embedding, metrics, err = pipeline.extract_face_embedding(sample_face_bytes)

        assert err is None
        assert embedding is not None
        assert len(embedding) == 128
        # Verify L2 normalization (unit length)
        import math
        norm = math.sqrt(sum(x * x for x in embedding))
        assert abs(norm - 1.0) < 1e-4

        # Verify extracted quality metrics
        assert metrics["quality_passed"] is True
        assert metrics["brightness"] > 0
        assert metrics["sharpness"] > 0
        assert metrics["width"] >= 80
        assert metrics["height"] >= 80
        assert "yaw" in metrics
        assert "pitch" in metrics

    def test_real_1_to_1_cosine_similarity_comparison(self, sample_face_bytes):
        """Verify cosine similarity is 1.0 for identical face and fails for mismatched embedding."""
        pipeline = FaceInferencePipeline()
        embedding, _, _ = pipeline.extract_face_embedding(sample_face_bytes)

        # Self match comparison
        self_similarity = compute_cosine_similarity(embedding, embedding)
        assert abs(self_similarity - 1.0) < 1e-5

        # Mismatched comparison (orthogonal or inverted vector)
        inverted = [-x for x in embedding]
        inv_sim = compute_cosine_similarity(embedding, inverted)
        assert inv_sim < 0.0

    def test_real_liveness_passive_pad(self, sample_face_bytes):
        """Verify MiniFASNet computes passive liveness score."""
        pipeline = FaceInferencePipeline()
        img = pipeline.decode_frame_bytes(sample_face_bytes)
        face, _ = pipeline.detect_face(img)
        assert face is not None

        bbox = face[:4]
        liveness_score = pipeline.compute_passive_liveness(img, bbox)
        assert isinstance(liveness_score, float)
        assert 0.0 <= liveness_score <= 1.0

    def test_admin_assisted_enrollment_from_camera_frame(
        self, test_employee, test_admin, sample_face_bytes
    ):
        """Verify enrollment extracts real embedding, encrypts with AES-256-GCM, and saves template."""
        # 1. Without consent, enrollment must fail
        with pytest.raises(Exception, match=r"consent|acknowledged"):
            enroll_face_from_camera_frame(
                target_user=test_employee,
                frame_data=sample_face_bytes,
                enrolled_by=test_admin,
            )

        # 2. Record digital consent
        record_biometric_acknowledgement(test_employee, version="1.0")

        # 3. Enroll directly from camera frame
        template, metrics = enroll_face_from_camera_frame(
            target_user=test_employee,
            frame_data=sample_face_bytes,
            enrolled_by=test_admin,
        )

        assert template.user == test_employee
        assert template.algorithm == "SFace-ONNX"
        assert template.key_id == "k1"
        assert template.encrypted_template is not None
        assert len(template.encrypted_template) > 512

        # 4. Verify decryption recovers identical 128d embedding
        kr = get_face_key_ring()
        decrypted_emb = decrypt_template(template.encrypted_template, kr)
        assert len(decrypted_emb) == 128

        pipeline = FaceInferencePipeline()
        live_emb, _, _ = pipeline.extract_face_embedding(sample_face_bytes)
        similarity = compute_cosine_similarity(decrypted_emb, live_emb)
        assert similarity >= 0.9999

    def test_end_to_end_verify_face_service(
        self, test_employee, test_admin, sample_face_bytes
    ):
        """Verify verify_face() service compares real live camera frames against enrolled template."""
        record_biometric_acknowledgement(test_employee, version="1.0")
        enroll_face_from_camera_frame(
            target_user=test_employee,
            frame_data=sample_face_bytes,
            enrolled_by=test_admin,
        )

        # Test verification passing real frame
        res = verify_face(
            user=test_employee,
            purpose=FaceVerificationLog.Purpose.ATTENDANCE_CHECK_IN,
            frame_data={"frame": sample_face_bytes},
        )

        # Confirm verification result
        assert res.similarity_score >= 0.70

        # Verify audit record created in FaceVerificationLog
        log = FaceVerificationLog.objects.filter(user=test_employee).latest("created_at")
        assert log.purpose == FaceVerificationLog.Purpose.ATTENDANCE_CHECK_IN
        assert log.similarity_score >= 0.70
        assert log.threshold_applied == 0.70
        assert log.duration_ms > 0

    def test_live_verify_api_endpoint(
        self, test_employee, test_admin, sample_face_base64
    ):
        """Test POST /api/face/verify/ endpoint with camera base64 data."""
        record_biometric_acknowledgement(test_employee, version="1.0")
        enroll_face_from_camera_frame(
            target_user=test_employee,
            frame_data=sample_face_base64,
            enrolled_by=test_admin,
        )

        client = Client()
        client.force_login(test_employee)

        # 1. Test client-side assertion rejection
        res_hack = client.post(
            reverse("face:verify"),
            data={"frame": sample_face_base64, "face_verified": True},
            content_type="application/json",
        )
        assert res_hack.status_code == 400
        assert "Security Invariant Violation" in res_hack.json()["error_reason"]

        # 2. Test legitimate verification
        res_ok = client.post(
            reverse("face:verify"),
            data={"frame": sample_face_base64, "purpose": "attendance_check_in"},
            content_type="application/json",
        )
        # Verify endpoint processed real metrics
        data = res_ok.json()
        assert "similarity_score" in data
        assert "match_threshold" in data
        assert "liveness_score" in data
        assert "decision" in data
        assert data["similarity_score"] >= 0.70

    def test_active_motion_challenge_api(self, test_employee):
        """Test POST /api/face/challenge/ issues random head-turn challenge."""
        client = Client()
        client.force_login(test_employee)

        res = client.post(reverse("face:challenge"))
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "issued"
        assert data["action"] in ["turn_left", "turn_right", "nod_down"]
        assert len(data["nonce"]) >= 16
        assert "instruction" in data
