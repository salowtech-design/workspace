"""
Active Liveness Challenge Protocol.
Generates and verifies time-limited, user-bound random head movement challenges.
Prevents presentation attacks using printed photographs or static video replays.
"""

import secrets
import time
from enum import Enum
from typing import NamedTuple


class ChallengeAction(str, Enum):
    TURN_LEFT = "turn_left"
    TURN_RIGHT = "turn_right"
    NOD_DOWN = "nod_down"


class ActiveChallenge(NamedTuple):
    nonce: str
    action: ChallengeAction
    issued_at: float
    expires_at: float
    user_id: str


CHALLENGE_TTL_SECONDS = 45.0
MIN_ANGLE_DELTA = 14.0  # degrees threshold for deliberate movement


def issue_challenge(user_id: str) -> ActiveChallenge:
    """Generate a single-use random motion challenge."""
    now = time.time()
    action = secrets.choice(
        [ChallengeAction.TURN_LEFT, ChallengeAction.TURN_RIGHT, ChallengeAction.NOD_DOWN]
    )
    nonce = secrets.token_urlsafe(16)
    return ActiveChallenge(
        nonce=nonce,
        action=action,
        issued_at=now,
        expires_at=now + CHALLENGE_TTL_SECONDS,
        user_id=user_id,
    )


def verify_active_challenge(
    challenge: ActiveChallenge,
    initial_yaw: float,
    initial_pitch: float,
    final_yaw: float,
    final_pitch: float,
    claimed_user_id: str,
) -> tuple[bool, str]:
    """
    Verify whether observed head rotation matches the issued active challenge.
    Checks user binding, expiry, and angular trajectory.
    """
    now = time.time()
    if now > challenge.expires_at:
        return False, "Active challenge expired. Please retry."

    if challenge.user_id != claimed_user_id:
        return False, "Challenge user binding mismatch."

    yaw_delta = final_yaw - initial_yaw
    pitch_delta = final_pitch - initial_pitch

    if challenge.action == ChallengeAction.TURN_LEFT:
        # Turning left increases positive yaw in standard camera projection
        if yaw_delta >= MIN_ANGLE_DELTA:
            return True, ""
        return False, f"Insufficient leftward head movement (observed {yaw_delta:.1f}°)."

    elif challenge.action == ChallengeAction.TURN_RIGHT:
        # Turning right decreases yaw (negative delta)
        if -yaw_delta >= MIN_ANGLE_DELTA:
            return True, ""
        return False, f"Insufficient rightward head movement (observed {-yaw_delta:.1f}°)."

    elif challenge.action == ChallengeAction.NOD_DOWN:
        if pitch_delta >= MIN_ANGLE_DELTA:
            return True, ""
        return False, f"Insufficient downward head movement (observed {pitch_delta:.1f}°)."

    return False, "Unknown challenge action."
