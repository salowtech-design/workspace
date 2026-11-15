"""
Production Biometric Decision Engine.
Evaluates multi-stage pipeline: Quality -> Liveness -> Active Challenge -> Identity Match.
"""

from typing import Sequence

from apps.face.engine.challenge import ActiveChallenge, verify_active_challenge
from apps.face.engine.embedder import compute_cosine_similarity
from apps.face.engine.protocols import VerificationResult
from apps.face.engine.quality import check_face_quality

MATCH_THRESHOLD = 0.70  # SFace cosine similarity threshold
PASSIVE_LIVENESS_THRESHOLD = 0.65  # MiniFASNet anti-spoof threshold floor


class ProductionFaceVerifier:
    """
    Pure Python verifier enforcing zero raw image retention, strict quality checks,
    and multi-stage liveness/identity verification.
    """

    def verify_attempt(
        self,
        enrolled_embedding: Sequence[float],
        live_embedding: Sequence[float],
        liveness_score: float,
        brightness: float,
        sharpness: float,
        face_width: int,
        face_height: int,
        initial_yaw: float = 0.0,
        initial_pitch: float = 0.0,
        final_yaw: float = 0.0,
        final_pitch: float = 0.0,
        challenge: ActiveChallenge = None,
        claimed_user_id: str = "",
    ) -> VerificationResult:
        # 1. Image & Pose Quality Check
        quality = check_face_quality(
            brightness=brightness,
            sharpness=sharpness,
            width=face_width,
            height=face_height,
            yaw=initial_yaw,
            pitch=initial_pitch,
        )
        if not quality.passed:
            return VerificationResult(
                success=False,
                confidence_score=0.0,
                liveness_score=liveness_score,
                method="face",
                error_reason=quality.reason,
            )

        # 2. Passive Presentation Attack Detection (Liveness)
        if liveness_score < PASSIVE_LIVENESS_THRESHOLD:
            return VerificationResult(
                success=False,
                confidence_score=0.0,
                liveness_score=liveness_score,
                method="face",
                error_reason="Liveness check failed: possible spoof or print attack detected.",
            )

        # 3. Active Motion Challenge (if provided)
        if challenge:
            challenge_ok, challenge_err = verify_active_challenge(
                challenge=challenge,
                initial_yaw=initial_yaw,
                initial_pitch=initial_pitch,
                final_yaw=final_yaw,
                final_pitch=final_pitch,
                claimed_user_id=claimed_user_id,
            )
            if not challenge_ok:
                return VerificationResult(
                    success=False,
                    confidence_score=0.0,
                    liveness_score=liveness_score,
                    method="face",
                    error_reason=f"Active challenge failed: {challenge_err}",
                )

        # 4. Identity Embedding Cosine Similarity
        similarity = compute_cosine_similarity(enrolled_embedding, live_embedding)
        if similarity >= MATCH_THRESHOLD:
            return VerificationResult(
                success=True,
                confidence_score=float(similarity),
                liveness_score=liveness_score,
                method="face",
                error_reason=None,
            )

        return VerificationResult(
            success=False,
            confidence_score=float(similarity),
            liveness_score=liveness_score,
            method="face",
            error_reason="Biometric identity verification failed: face does not match profile.",
        )
