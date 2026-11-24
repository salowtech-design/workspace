"""
Face Recognition System (FRS) API Controllers.
Provides high-performance, live server-side biometric endpoints for any device camera.
Zero raw image retention invariant strictly enforced: frames are processed in volatile RAM only.
"""

import json
import logging
import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import CustomUser
from apps.face.engine.challenge import (
    ActiveChallenge,
    ChallengeAction,
    issue_challenge,
)
from apps.face.engine.pipeline import FaceInferencePipeline
from apps.face.models import (
    BiometricNoticeAcknowledgement,
    FaceTemplate,
    FaceVerificationLog,
)
from apps.face.services import (
    enroll_face_from_camera_frame,
    record_biometric_acknowledgement,
    verify_face,
)

logger = logging.getLogger("apps.face.views")


@require_GET
def face_system_status_view(request):
    """
    Public health & readiness diagnostic endpoint.
    Reports operational status of neural networks, loaded weights, and licensing.
    """
    pipeline = FaceInferencePipeline()
    models_ready = pipeline.models_available

    return JsonResponse(
        {
            "status": "ready" if models_ready else "degraded",
            "models_loaded": models_ready,
            "models": {
                "face_detection": "YuNet (OpenCV)",
                "face_recognition": "SFace (CASIA / OpenCV)",
                "presentation_attack_detection": "MiniFASNetV2",
            },
            "licenses": FaceInferencePipeline.MODEL_LICENSES,
            "security_invariants": {
                "one_to_one_verification_only": True,
                "zero_raw_image_retention": True,
                "aes256_gcm_template_encryption": True,
                "server_side_ground_truth": True,
            },
        }
    )


@login_required
@require_POST
def issue_active_challenge_view(request):
    """
    Issues a server-side active motion challenge to prevent video replay / print spoofs.
    Returns random directive ('turn_left', 'turn_right', 'nod_down') and cryptographic nonce.
    """
    target_user_id = request.user.employee_id
    challenge = issue_challenge(user_id=target_user_id)

    instructions = {
        ChallengeAction.TURN_LEFT: "Please turn your head slightly to the left.",
        ChallengeAction.TURN_RIGHT: "Please turn your head slightly to the right.",
        ChallengeAction.NOD_DOWN: "Please nod your head slightly downward.",
    }

    # Store challenge in session for verification binding
    request.session[f"face_challenge_{target_user_id}"] = {
        "challenge_id": challenge.nonce,
        "nonce": challenge.nonce,
        "action": challenge.action.value,
        "issued_at": challenge.issued_at,
        "expires_at": challenge.expires_at,
        "user_id": challenge.user_id,
    }

    return JsonResponse(
        {
            "status": "issued",
            "challenge_id": challenge.nonce,
            "action": challenge.action.value,
            "instruction": instructions.get(challenge.action, "Follow head motion prompt."),
            "nonce": challenge.nonce,
            "expires_at": challenge.expires_at,
        }
    )


@login_required
@require_POST
def verify_live_face_view(request):
    """
    Receives camera frame(s) from any device camera (phone, tablet, laptop, desktop).
    Executes real FRS inference pipeline, compares against user's encrypted template,
    and returns authoritative biometric metrics.
    """
    try:
        # Reject client-side assertions immediately
        if request.content_type == "application/json":
            payload = json.loads(request.body.decode("utf-8") or "{}")
        else:
            payload = request.POST.dict()

        FaceInferencePipeline.reject_client_side_override(payload)

        # Parse frame data
        frame_input = payload.get("frame")
        frames_list = payload.get("frames")
        if not frame_input and not frames_list and request.FILES:
            uploaded_file = request.FILES.get("frame")
            if uploaded_file:
                frame_input = uploaded_file.read()

        if not frame_input and not frames_list:
            return JsonResponse(
                {
                    "success": False,
                    "decision": "FAILURE",
                    "error_reason": "No camera frame buffer provided in request.",
                },
                status=400,
            )

        # Retrieve optional active challenge
        target_user = request.user
        challenge_data = request.session.get(f"face_challenge_{target_user.employee_id}")
        challenge_obj = None
        if challenge_data:
            challenge_obj = ActiveChallenge(
                nonce=challenge_data["nonce"],
                action=ChallengeAction(challenge_data["action"]),
                issued_at=float(challenge_data["issued_at"]),
                expires_at=float(challenge_data["expires_at"]),
                user_id=challenge_data["user_id"],
            )

        purpose = payload.get("purpose", FaceVerificationLog.Purpose.ATTENDANCE_CHECK_IN)
        if purpose not in [p.value for p in FaceVerificationLog.Purpose]:
            purpose = FaceVerificationLog.Purpose.ATTENDANCE_CHECK_IN

        frame_data_dict = {
            "frame": frame_input,
            "frames": frames_list,
            "challenge": challenge_obj,
        }

        result = verify_face(user=target_user, purpose=purpose, frame_data=frame_data_dict)

        # Clean up consumed challenge
        if f"face_challenge_{target_user.employee_id}" in request.session:
            del request.session[f"face_challenge_{target_user.employee_id}"]

        return JsonResponse(
            {
                "success": result.success,
                "decision": "SUCCESS" if result.success else "FAILURE",
                "similarity_score": round(result.similarity_score, 4),
                "match_threshold": 0.70 if purpose != FaceVerificationLog.Purpose.STEP_UP else 0.85,
                "liveness_score": round(result.liveness_score, 4),
                "liveness_threshold": 0.65,
                "quality_passed": result.quality_passed,
                "method": result.method,
                "error_reason": result.error_reason,
            },
            status=200 if result.success else 400,
        )

    except ValueError as val_err:
        return JsonResponse(
            {"success": False, "decision": "SECURITY_VIOLATION", "error_reason": str(val_err)},
            status=400,
        )
    except Exception as exc:
        logger.exception("Error during live biometric verification: %s", exc)
        return JsonResponse(
            {"success": False, "decision": "ERROR", "error_reason": f"Verification failed: {exc}"},
            status=500,
        )


@login_required
@require_POST
def enroll_face_from_device_view(request):
    """
    Admin-assisted live face enrollment from any camera device.
    Extracts real 128d embedding, encrypts via AES-256-GCM, and updates FaceTemplate.
    """
    if request.user.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        return JsonResponse(
            {"success": False, "error_reason": "Face enrollment must be admin-assisted."},
            status=403,
        )

    try:
        if request.content_type == "application/json":
            payload = json.loads(request.body.decode("utf-8") or "{}")
        else:
            payload = request.POST.dict()

        target_emp_id = payload.get("target_employee_id")
        if not target_emp_id:
            return JsonResponse(
                {"success": False, "error_reason": "target_employee_id is required."},
                status=400,
            )

        target_user = CustomUser.objects.filter(employee_id=target_emp_id).first()
        if not target_user:
            return JsonResponse(
                {"success": False, "error_reason": f"Employee {target_emp_id} not found."},
                status=404,
            )

        # Check consent
        has_consented = BiometricNoticeAcknowledgement.objects.filter(user=target_user).exists()
        consent_flag = payload.get("consent_acknowledged")
        if not has_consented:
            if consent_flag:
                record_biometric_acknowledgement(
                    user=target_user,
                    version="1.0",
                    ip_address=request.META.get("REMOTE_ADDR"),
                )
            else:
                return JsonResponse(
                    {
                        "success": False,
                        "error_reason": "Biometric notice consent must be acknowledged prior to enrollment.",
                    },
                    status=400,
                )

        # Parse frame
        frame_input = payload.get("frame")
        if not frame_input and request.FILES:
            uploaded = request.FILES.get("frame")
            if uploaded:
                frame_input = uploaded.read()

        if not frame_input:
            return JsonResponse(
                {"success": False, "error_reason": "Camera frame is required for enrollment."},
                status=400,
            )

        template, metrics = enroll_face_from_camera_frame(
            target_user=target_user,
            frame_data=frame_input,
            enrolled_by=request.user,
        )

        return JsonResponse(
            {
                "success": True,
                "employee_id": target_user.employee_id,
                "full_name": target_user.full_name,
                "algorithm": template.algorithm,
                "key_id": template.key_id,
                "quality_score": round(template.quality_score, 4),
                "metrics": {
                    "brightness": round(metrics.get("brightness", 0.0), 2),
                    "sharpness": round(metrics.get("sharpness", 0.0), 2),
                    "face_width": metrics.get("width", 0),
                    "face_height": metrics.get("height", 0),
                    "estimated_yaw": round(metrics.get("yaw", 0.0), 2),
                },
                "enrolled_at": template.updated_at.isoformat(),
            }
        )

    except ValidationError as val_err:
        return JsonResponse(
            {"success": False, "error_reason": str(val_err.message if hasattr(val_err, "message") else val_err)},
            status=400,
        )
    except Exception as exc:
        logger.exception("Error during face enrollment: %s", exc)
        return JsonResponse(
            {"success": False, "error_reason": f"Enrollment failed: {exc}"},
            status=500,
        )


@login_required
@require_GET
def employee_face_status_view(request, employee_id: str):
    """Returns enrollment status and biometric history summary for an employee."""
    target_user = CustomUser.objects.filter(employee_id=employee_id).first()
    if not target_user:
        return JsonResponse({"error": "Employee not found"}, status=404)

    # Self or admin view
    if request.user != target_user and request.user.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        return JsonResponse({"error": "Forbidden"}, status=403)

    template = FaceTemplate.objects.filter(user=target_user).first()
    has_consent = BiometricNoticeAcknowledgement.objects.filter(user=target_user).exists()
    recent_logs = FaceVerificationLog.objects.filter(user=target_user)[:5]

    return JsonResponse(
        {
            "employee_id": target_user.employee_id,
            "has_consent": has_consent,
            "is_enrolled": template is not None,
            "algorithm": template.algorithm if template else None,
            "quality_score": round(template.quality_score, 4) if template else None,
            "enrolled_at": template.created_at.isoformat() if template else None,
            "recent_verifications": [
                {
                    "purpose": log.purpose,
                    "decision": log.decision,
                    "similarity_score": round(log.similarity_score, 4),
                    "duration_ms": round(log.duration_ms, 2),
                    "created_at": log.created_at.isoformat(),
                }
                for log in recent_logs
            ],
        }
    )
