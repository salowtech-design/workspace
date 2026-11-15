from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class VerificationResult:
    """
    Immutable value object returned by face verification engines.
    Contains no raw frame data, embeddings, or biometric templates.
    """

    success: bool
    confidence_score: float = 0.0
    liveness_score: float = 0.0
    method: str = "face"  # 'face' or 'dev_stub'
    error_reason: Optional[str] = None
    similarity_score: float = 0.0
    quality_passed: bool = True

    def __post_init__(self):
        if self.similarity_score and not self.confidence_score:
            object.__setattr__(self, "confidence_score", self.similarity_score)
        elif self.confidence_score and not self.similarity_score:
            object.__setattr__(self, "similarity_score", self.confidence_score)


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
