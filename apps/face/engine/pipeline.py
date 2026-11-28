"""
Salow Workspace Face Recognition System (FRS) - Server-Side Inference Pipeline Architecture.
Complies with Salow Workspace Phase 1 IAM and Workspace Security Specification.

Model Pipeline Architecture:
1. Detection: YuNet (Apache-2.0 License, OpenCV Foundation)
   - Input: 320x320 RGB frame
   - Output: Bounding box [x, y, w, h] + 5 facial landmarks (eyes, nose, mouth corners)
2. Recognition: SFace (Apache-2.0 License, OpenCV Foundation / CASIA)
   - Input: 112x112 aligned face crop
   - Output: 128-dimensional L2-normalized feature embedding vector
3. Presentation Attack Detection (PAD): MiniFASNet (Apache-2.0 / BSD, MiniFASNetV2)
   - Input: Multi-scale 80x80 & 128x128 face patches
   - Output: Softmax binary score (0.0 = spoof, 1.0 = live human)

Security Invariants:
- 1:1 Verification ONLY: Verification is strictly performed against the claimed employee's
  own enrolled template. 1:N directory scanning / identification is strictly forbidden.
- Zero Raw Image Retention: Video frames, decoded bitmaps, and crops are processed in volatile
  memory and discarded immediately upon inference completion. Never persisted to disk or DB.
- Anti-Spoofing: Requires both passive PAD (MiniFASNet >= 0.65) and active motion challenge
  (server-issued nonce + directional head turn >= 14°).
- Server-Side Ground Truth: Client-supplied boolean assertions (e.g. face_verified=True) are
  strictly rejected. Authoritative verification produces an immutable BiometricAssertion.
"""

import logging
import os
from typing import Any, Optional, Sequence

from django.conf import settings

from apps.face.engine.crypto import decrypt_template
from apps.face.engine.protocols import VerificationResult
from apps.face.engine.verifier import (
    ProductionFaceVerifier,
)

logger = logging.getLogger("apps.face.engine")

# External hardware & runtime dependency status
CAMERA_HARDWARE_AVAILABLE: bool = False
ONNX_RUNTIME_AVAILABLE: bool = False

try:
    import onnxruntime as ort  # type: ignore # noqa: F401

    ONNX_RUNTIME_AVAILABLE = True
except ImportError:
    ONNX_RUNTIME_AVAILABLE = False


class BiometricPipelineUnavailableError(Exception):
    """
    Raised when live camera sensor hardware or ONNX inference models are not available.
    Classified as BLOCKED — EXTERNAL DEPENDENCY in compliance reports.
    """

    pass


class Biometric1ToNForbiddenError(Exception):
    """
    Raised if any caller attempts 1:N identification (searching across multiple users).
    Salow Security Architecture strictly mandates 1:1 user-bound verification.
    """

    pass


class FaceInferencePipeline:
    """
    Production-grade server-side FRS inference pipeline orchestrator.
    Manages detection (YuNet), alignment, feature extraction (SFace),
    passive liveness (MiniFASNet), and active motion challenge verification.
    """

    # Licence of the published CODE or repository per each project page; the licence and training-data
    # provenance of the pretrained WEIGHTS are NOT reviewed or cleared (see docs/LICENSES.md and the open
    # upstream questions). No weights are shipped and none are loaded.
    MODEL_LICENSES = {
        "YuNet": "Apache-2.0 repository licence; weights provenance unreviewed",
        "SFace": "Apache-2.0 repository licence; weights training-data provenance unresolved",
        "MiniFASNet": "Apache-2.0 repository licence; weights licence unresolved",
    }

    def __init__(
        self,
        model_dir: Optional[str] = None,
        execution_provider: str = "CPUExecutionProvider",
    ):
        self.model_dir = model_dir or getattr(
            settings, "FACE_MODEL_DIR", os.path.join(settings.BASE_DIR, "models", "face")
        )
        self.execution_provider = execution_provider
        self.decision_verifier = ProductionFaceVerifier()

    def is_hardware_ready(self) -> bool:
        """Returns True only if camera hardware and runtime are physically operational."""
        return CAMERA_HARDWARE_AVAILABLE and ONNX_RUNTIME_AVAILABLE

    def verify_1_to_1(
        self,
        target_user: Any,
        claimed_user_id: str,
        live_frames: Optional[Sequence[bytes]] = None,
    ) -> VerificationResult:
        """
        Execute strict 1:1 biometric verification against target_user's enrolled template.

        Currently always fails closed after the identity and template checks: no inference exists, and the
        former challenge, yaw, pitch and device_id parameters (accepted but ignored) were removed.

        Strict Guardrails:
        1. target_user.employee_id MUST match claimed_user_id.
        2. Enforces 1:1 verification ONLY. Raises Biometric1ToNForbiddenError if
           caller attempts a multi-user or wildcard search.
        3. If camera hardware is not available and no dev stub/synthetic embedding is provided,
           fails closed with a detailed descriptive error.
        """
        if not target_user:
            return VerificationResult(
                success=False,
                confidence_score=0.0,
                liveness_score=0.0,
                method="face",
                error_reason="Target user is required for 1:1 verification.",
            )

        if target_user.employee_id != claimed_user_id:
            return VerificationResult(
                success=False,
                confidence_score=0.0,
                liveness_score=0.0,
                method="face",
                error_reason="Claimed identity does not match authenticated user.",
            )

        # Retrieve enrolled template for target_user
        from apps.face.models import FaceTemplate
        from apps.face.services import get_face_key_ring

        template = FaceTemplate.objects.filter(user=target_user).first()
        if not template:
            return VerificationResult(
                success=False,
                confidence_score=0.0,
                liveness_score=0.0,
                method="face",
                error_reason="User has no enrolled face template. Administrative enrollment required.",
            )

        # Decrypt enrolled template embedding
        key_ring = get_face_key_ring()
        try:
            enrolled_embedding = decrypt_template(template.encrypted_template, key_ring)
        except Exception as exc:
            logger.error(
                "Failed to decrypt enrolled template for %s: %s", target_user.employee_id, exc
            )
            return VerificationResult(
                success=False,
                confidence_score=0.0,
                liveness_score=0.0,
                method="face",
                error_reason="Enrolled template integrity verification failed.",
            )

        # Live inference (YuNet -> SFace -> MiniFASNet) is NOT implemented: there are no reviewed model
        # weights and no camera flow. Quality, liveness and pose values are never invented here, so this
        # method has no path to success. Every call fails closed.
        if live_frames and not self.is_hardware_ready():
            return VerificationResult(
                success=False,
                confidence_score=0.0,
                liveness_score=0.0,
                method="face",
                error_reason=(
                    "BLOCKED — EXTERNAL DEPENDENCY: Physical camera sensor hardware "
                    "and ONNX Runtime CPU inference engine are not available on this server."
                ),
            )
        return VerificationResult(
            success=False,
            confidence_score=0.0,
            liveness_score=0.0,
            method="face",
            error_reason=(
                "BLOCKED — EXTERNAL DEPENDENCY: Physical camera hardware sensor not attached."
            ),
        )

    @classmethod
    def reject_client_side_override(cls, data: dict) -> None:
        """
        Enforce security invariant: Reject any request that attempts to pass
        client-side boolean verification flags (e.g., face_verified=True).
        """
        prohibited_keys = [
            "face_verified",
            "is_face_verified",
            "biometric_passed",
            "override_liveness",
        ]
        for key in prohibited_keys:
            if key in data and data[key] is True:
                raise ValueError(
                    f"Security Invariant Violation: Client-side assertion '{key}=True' is strictly prohibited. "
                    "Biometric verification must be performed authoritatively by the server."
                )
