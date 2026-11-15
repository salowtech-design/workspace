from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class VerificationResult:
    """
    Immutable value object returned by face verification engines.
    Contains no raw frame data, embeddings, or biometric templates.
    """

    success: bool
    confidence_score: float
    liveness_score: float
    method: str  # 'face' or 'dev_stub'
    error_reason: Optional[str] = None


class FaceVerifier(Protocol):
    """
    Pure Python face verification protocol contract.
    Decoupled from Django, HTTP requests, and ORM.
    """

    def verify(
        self,
        user_id: str,
        challenge_nonce: str,
        frames: list[bytes],
    ) -> VerificationResult: ...
