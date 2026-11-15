import pytest

from apps.face.engine.challenge import (
    ActiveChallenge,
    ChallengeAction,
    issue_challenge,
    verify_active_challenge,
)
from apps.face.engine.crypto import (
    BiometricKeyRing,
    decrypt_template,
    encrypt_template,
)
from apps.face.engine.embedder import (
    generate_synthetic_embedding,
)
from apps.face.engine.quality import check_face_quality
from apps.face.engine.verifier import ProductionFaceVerifier


def test_biometric_template_encryption_and_tamper_detection():
    """Verify AES-256-GCM encryption round-trip and authenticated tamper detection."""
    key_ring = BiometricKeyRing(
        keys={"v1": b"01234567890123456789012345678901"},
        active_key_id="v1",
    )
    original_embedding = generate_synthetic_embedding(seed=42)

    # 1. Encrypt
    ciphertext = encrypt_template(original_embedding, key_ring)
    assert isinstance(ciphertext, bytes)
    assert len(ciphertext) > 512

    # 2. Decrypt
    decrypted = decrypt_template(ciphertext, key_ring)
    assert len(decrypted) == 128
    for o, d in zip(original_embedding, decrypted, strict=False):
        assert abs(o - d) < 1e-6

    # 3. Tamper detection: modifying 1 bit must fail authentication
    tampered = bytearray(ciphertext)
    tampered[-5] ^= 0xFF  # Flip bits in tag/ciphertext
    with pytest.raises(ValueError, match="integrity check failed"):
        decrypt_template(bytes(tampered), key_ring)


def test_biometric_key_rotation():
    """Verify key ring can decrypt templates encrypted with older active keys."""
    key_v1 = b"11111111111111111111111111111111"
    key_v2 = b"22222222222222222222222222222222"

    ring_v1 = BiometricKeyRing(keys={"v1": key_v1}, active_key_id="v1")
    embedding = generate_synthetic_embedding(seed=101)
    ciphertext_v1 = encrypt_template(embedding, ring_v1)

    # Rotated key ring: v2 is now active, v1 preserved for decryption
    ring_v2 = BiometricKeyRing(keys={"v1": key_v1, "v2": key_v2}, active_key_id="v2")

    decrypted = decrypt_template(ciphertext_v1, ring_v2)
    assert abs(decrypted[0] - embedding[0]) < 1e-6


def test_face_quality_assessment():
    """Verify quality filter rejects poor lighting, blur, low resolution, or tilt."""
    # Good quality
    good = check_face_quality(brightness=100.0, sharpness=80.0, width=120, height=120)
    assert good.passed is True

    # Low resolution
    small = check_face_quality(brightness=100.0, sharpness=80.0, width=60, height=60)
    assert small.passed is False
    assert "too small" in small.reason

    # Underexposed
    dark = check_face_quality(brightness=20.0, sharpness=80.0, width=100, height=100)
    assert dark.passed is False
    assert "too dark" in dark.reason

    # Blurry
    blurry = check_face_quality(brightness=100.0, sharpness=25.0, width=100, height=100)
    assert blurry.passed is False
    assert "blurry" in blurry.reason

    # Excessive tilt
    tilted = check_face_quality(brightness=100.0, sharpness=80.0, width=100, height=100, yaw=45.0)
    assert tilted.passed is False
    assert "angle is too tilted" in tilted.reason


def test_active_liveness_challenge_verification():
    """Verify motion challenge enforces angle delta, user binding, and expiry."""
    challenge = issue_challenge(user_id="EMP001")
    assert challenge.user_id == "EMP001"

    # User mismatch
    passed, reason = verify_active_challenge(
        challenge=challenge,
        initial_yaw=0.0,
        initial_pitch=0.0,
        final_yaw=20.0,
        final_pitch=0.0,
        claimed_user_id="EMP999",
    )
    assert passed is False
    assert "binding mismatch" in reason

    # Challenge Left
    left_challenge = ActiveChallenge(
        nonce="test",
        action=ChallengeAction.TURN_LEFT,
        issued_at=100.0,
        expires_at=9999999999.0,
        user_id="EMP001",
    )
    # 0 -> 20 deg left (positive delta)
    passed, _ = verify_active_challenge(left_challenge, 0.0, 0.0, 20.0, 0.0, "EMP001")
    assert passed is True

    # Insufficient turn
    passed, reason = verify_active_challenge(left_challenge, 0.0, 0.0, 5.0, 0.0, "EMP001")
    assert passed is False
    assert "Insufficient leftward" in reason


def test_production_verifier_decision_logic():
    """Verify multi-stage decision logic on synthetic embeddings."""
    verifier = ProductionFaceVerifier()

    person_a_enrolled = generate_synthetic_embedding(seed=1)
    person_a_live = [x + 0.01 for x in person_a_enrolled]  # Near match
    person_b_live = generate_synthetic_embedding(seed=99)  # Different person

    # 1. Valid match
    res_match = verifier.verify_attempt(
        enrolled_embedding=person_a_enrolled,
        live_embedding=person_a_live,
        liveness_score=0.92,
        brightness=100.0,
        sharpness=85.0,
        face_width=120,
        face_height=120,
    )
    assert res_match.success is True
    assert res_match.confidence_score >= 0.70

    # 2. Identity mismatch
    res_mismatch = verifier.verify_attempt(
        enrolled_embedding=person_a_enrolled,
        live_embedding=person_b_live,
        liveness_score=0.92,
        brightness=100.0,
        sharpness=85.0,
        face_width=120,
        face_height=120,
    )
    assert res_mismatch.success is False
    assert "does not match" in res_mismatch.error_reason

    # 3. Spoof attack (low liveness)
    res_spoof = verifier.verify_attempt(
        enrolled_embedding=person_a_enrolled,
        live_embedding=person_a_live,
        liveness_score=0.40,  # Below threshold
        brightness=100.0,
        sharpness=85.0,
        face_width=120,
        face_height=120,
    )
    assert res_spoof.success is False
    assert "spoof" in res_spoof.error_reason
