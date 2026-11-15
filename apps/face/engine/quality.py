"""
Face Image & Frame Quality Assessment.
Evaluates brightness, sharpness, minimum face resolution, and pose angle bounds.
"""

from typing import NamedTuple


class QualityCheckResult(NamedTuple):
    passed: bool
    brightness: float
    sharpness: float
    face_width: int
    face_height: int
    reason: str = ""


# Quality Thresholds
MIN_FACE_WIDTH = 80
MIN_FACE_HEIGHT = 80
MIN_BRIGHTNESS = 40.0
MAX_BRIGHTNESS = 220.0
MIN_SHARPNESS = 45.0  # Laplacian variance floor


def check_face_quality(
    brightness: float,
    sharpness: float,
    width: int,
    height: int,
    yaw: float = 0.0,
    pitch: float = 0.0,
) -> QualityCheckResult:
    """
    Evaluate bounding box resolution, lighting, sharpness, and pose bounds.
    Pure arithmetic logic independent of external UI/framework.
    """
    if width < MIN_FACE_WIDTH or height < MIN_FACE_HEIGHT:
        return QualityCheckResult(
            passed=False,
            brightness=brightness,
            sharpness=sharpness,
            face_width=width,
            face_height=height,
            reason=(
                f"Face bounding box too small ({width}x{height}px; "
                f"min {MIN_FACE_WIDTH}x{MIN_FACE_HEIGHT}px required)."
            ),
        )

    if brightness < MIN_BRIGHTNESS:
        return QualityCheckResult(
            passed=False,
            brightness=brightness,
            sharpness=sharpness,
            face_width=width,
            face_height=height,
            reason="Image is too dark (underexposed). Please move to better lighting.",
        )

    if brightness > MAX_BRIGHTNESS:
        return QualityCheckResult(
            passed=False,
            brightness=brightness,
            sharpness=sharpness,
            face_width=width,
            face_height=height,
            reason="Image is too bright (overexposed glare). Please avoid direct backlight.",
        )

    if sharpness < MIN_SHARPNESS:
        return QualityCheckResult(
            passed=False,
            brightness=brightness,
            sharpness=sharpness,
            face_width=width,
            face_height=height,
            reason="Face is blurry or out of focus. Please hold the camera steady.",
        )

    if abs(yaw) > 30.0 or abs(pitch) > 30.0:
        return QualityCheckResult(
            passed=False,
            brightness=brightness,
            sharpness=sharpness,
            face_width=width,
            face_height=height,
            reason="Face angle is too tilted. Please face the camera directly.",
        )

    return QualityCheckResult(
        passed=True,
        brightness=brightness,
        sharpness=sharpness,
        face_width=width,
        face_height=height,
    )
