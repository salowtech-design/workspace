from apps.face.engine.protocols import FaceVerifier, VerificationResult


class StubVerifier(FaceVerifier):
    """
    Development-only verifier stub for local testing of attendance flows.
    MUST NEVER be active or accessible in production environments.
    """

    def __init__(self, allowed: bool = False, force_failure: bool = False):
        if not allowed:
            raise RuntimeError(
                "SECURITY ALERT: StubVerifier cannot be instantiated when DEV_VERIFIER is disabled!"
            )
        self.force_failure = force_failure

    def verify(
        self,
        user_id: str,
        challenge_nonce: str,
        frames: list[bytes],
    ) -> VerificationResult:
        if self.force_failure:
            return VerificationResult(
                success=False,
                confidence_score=0.10,
                liveness_score=0.10,
                method="dev_stub",
                error_reason="Stub verification forced failure.",
            )

        return VerificationResult(
            success=True,
            confidence_score=0.99,
            liveness_score=0.98,
            method="dev_stub",
            error_reason=None,
        )
