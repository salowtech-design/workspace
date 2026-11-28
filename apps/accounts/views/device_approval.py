"""
Views for Second-Device Approval Workflow — Salow Workspace IAM.
Implements:
1. Device B waiting screen with HTMX polling (`hx-trigger="every 2s"`).
2. Device A in-application decision (approve/reject).
3. Admin fallback decision with dual-control protection for Super Admin.
4. Company email OTP verification.
5. Server-side face verification check and session establishment.
"""

import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.auth.device_approval import (
    approve_device_by_admin,
    approve_device_by_existing_session,
    check_and_expire_request,
    complete_device_verification_and_login,
    reject_device_request,
    verify_device_email_otp,
)
from apps.accounts.auth.errors import AuthException
from apps.accounts.models import CustomUser, DeviceApprovalRequest


def device_approval_wait_view(request, request_id: uuid.UUID):
    """Device B: Renders waiting screen that polls status every 2 seconds via HTMX."""
    req = DeviceApprovalRequest.objects.filter(request_id=request_id).first()
    if not req:
        messages.error(request, "Invalid or expired device approval request.")
        return redirect("accounts:login")

    check_and_expire_request(req)
    if req.status == DeviceApprovalRequest.Status.APPROVED:
        return redirect("accounts:device_email_otp", request_id=req.request_id)
    elif req.status in (
        DeviceApprovalRequest.Status.EXPIRED,
        DeviceApprovalRequest.Status.REJECTED,
    ):
        return render(
            request,
            "accounts/devices/approval_expired.html",
            {"req": req, "status": req.status},
        )

    return render(
        request,
        "accounts/devices/approval_wait.html",
        {"req": req, "user": req.user},
    )


def device_approval_status_view(request, request_id: uuid.UUID):
    """HTMX polling endpoint for device approval status (`hx-trigger="every 2s"`)."""
    req = DeviceApprovalRequest.objects.filter(request_id=request_id).first()
    if not req:
        return HttpResponse('<div class="alert alert-error">Request not found.</div>', status=404)

    check_and_expire_request(req)

    if req.status == DeviceApprovalRequest.Status.APPROVED:
        # Client-side HTMX redirect to next step (Email OTP verification)
        response = HttpResponse(
            '<div class="alert alert-success">Approved! Redirecting to email verification...</div>'
        )
        response["HX-Redirect"] = f"/auth/devices/approval/{req.request_id}/email-otp/"
        return response
    elif req.status == DeviceApprovalRequest.Status.REJECTED:
        response = HttpResponse(
            '<div class="alert alert-error">Request rejected by existing session.</div>'
        )
        response["HX-Redirect"] = f"/auth/devices/approval/{req.request_id}/wait/"
        return response
    elif req.status == DeviceApprovalRequest.Status.EXPIRED:
        response = HttpResponse(
            '<div class="alert alert-error">Request expired after 60 seconds.</div>'
        )
        response["HX-Redirect"] = f"/auth/devices/approval/{req.request_id}/wait/"
        return response

    return HttpResponse(
        f'<div class="status-indicator-pending s-73849dbd">'
        f"● Awaiting approval from active device ({req.requesting_device_ip or 'Remote'})..."
        f"</div>"
    )


@login_required
def check_pending_device_approval_view(request):
    """
    Device A: Polling endpoint (`hx-trigger="every 3s"`) that checks if a secondary
    device is requesting approval for this user's account (Section 5.1).
    Renders modal popup with transaction-bound number matching when pending.
    """
    from django.utils import timezone

    now = timezone.now()
    pending_req = (
        DeviceApprovalRequest.objects.filter(
            user=request.user,
            status=DeviceApprovalRequest.Status.PENDING,
            expires_at__gt=now,
        )
        .order_by("-created_at")
        .first()
    )

    if not pending_req:
        return HttpResponse("")

    return render(
        request,
        "accounts/devices/device_a_approval_modal.html",
        {
            "req": pending_req,
            "number_challenge": pending_req.number_challenge,
        },
    )


@login_required
@require_POST
def device_decision_view(request, request_id: uuid.UUID):
    """Device A: Approve or Reject requesting secondary device."""
    decision = request.POST.get("decision", "").strip().lower()
    reason = request.POST.get("reason", "Rejected from primary device").strip()

    req = DeviceApprovalRequest.objects.filter(request_id=request_id).first()
    if not req:
        messages.error(request, "Device request not found.")
        return redirect("attendance:panel")

    try:
        if decision == "approve":
            challenge_num = request.POST.get("matching_number")
            submitted_number = (
                int(challenge_num) if challenge_num and challenge_num.isdigit() else None
            )
            approve_device_by_existing_session(
                request_id,
                request.user,
                submitted_number=submitted_number,
                approving_device_id=request.session.session_key or "",
            )
            messages.success(
                request, f"Secondary device approved. Verification code sent to {req.user.email}."
            )
        else:
            reject_device_request(request_id, request.user, reason=reason)
            messages.info(request, "Secondary device request was rejected.")
    except (AuthException, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect("attendance:panel")


def device_email_otp_view(request, request_id: uuid.UUID):
    """Device B: Step 2 — Enter one-time email verification code."""
    req = DeviceApprovalRequest.objects.filter(request_id=request_id).first()
    if not req or req.status != DeviceApprovalRequest.Status.APPROVED:
        messages.error(request, "Device approval request not in approved state.")
        return redirect("accounts:login")

    error = None
    if request.method == "POST":
        otp = request.POST.get("email_otp", "").strip()
        try:
            verified = verify_device_email_otp(request_id, otp)
        except AuthException as exc:
            if getattr(exc, "code", "") != "AUTH-LOG-002":
                raise  # only the lock case is shown; anything else is not turned into page text
            verified = False
            error = exc.user_message  # built in lockout.py from a whole number of minutes only
        else:
            if not verified:
                error = "Invalid or expired email OTP. Please check your inbox and try again."
        if verified:
            return redirect("accounts:device_face_verify", request_id=req.request_id)

    return render(
        request,
        "accounts/devices/email_otp.html",
        {"req": req, "user": req.user, "error": error},
    )


def device_face_verify_view(request, request_id: uuid.UUID):
    """Device B: Step 3 — Server-side face verification check and session establishment."""
    from django.conf import settings

    from apps.face.engine.stub import StubVerifier
    from apps.face.models import FaceTemplate, FaceVerificationLog

    req = DeviceApprovalRequest.objects.filter(request_id=request_id).first()
    if not req or not req.email_otp_verified:
        messages.error(request, "Prior verification steps not completed.")
        return redirect("accounts:login")

    has_template = FaceTemplate.objects.filter(user=req.user).exists()
    is_dev = getattr(settings, "DEV_VERIFIER", False)
    error = None

    if request.method == "POST":
        # Execute face verification gate (Section 9 & 17)
        face_ok = False
        if is_dev:
            stub = StubVerifier(allowed=True)
            res = stub.verify(
                user_id=req.user.employee_id, challenge_nonce="dev-secondary-nonce", frames=[]
            )
            face_ok = res.success
        elif has_template:
            from apps.face.services import verify_face

            res = verify_face(user=req.user, purpose=FaceVerificationLog.Purpose.STEP_UP)
            face_ok = res.success
            if not face_ok:
                error = (
                    res.error_reason
                    or "Facial recognition similarity score below security threshold."
                )
        else:
            error = "Biometric face template not enrolled for this account. Contact your administrator for fallback authorization."

        if face_ok:
            try:
                from apps.accounts.auth.device_approval import issue_device_biometric_assertion

                assertion = issue_device_biometric_assertion(
                    user=req.user,
                    approval_request=req,
                    challenge_nonce="server-challenge-face",
                    outcome="verified",
                    confidence_score=getattr(res, "confidence_score", 0.95),
                    liveness_score=getattr(res, "liveness_score", 0.90),
                    device_id=req.requesting_device_id or "",
                )
                complete_device_verification_and_login(
                    request,
                    request_id,
                    assertion_token=assertion.single_use_nonce,
                )
                messages.success(
                    request, "Secondary device verified successfully. Welcome to Salow Workspace!"
                )
                return redirect("attendance:panel")
            except AuthException as e:
                error = e.user_message

    return render(
        request,
        "accounts/devices/face_verify.html",
        {"req": req, "user": req.user, "error": error, "has_template": has_template},
    )


@login_required
@require_POST
def device_admin_approve_view(request, request_id: uuid.UUID):
    """Admin Console: Execute administrator fallback approval for lost/broken primary device."""
    if request.user.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can approve device fallbacks.")

    second_admin_id = request.POST.get("second_admin_id")
    second_admin = None
    if second_admin_id:
        second_admin = CustomUser.objects.filter(id=second_admin_id).first()

    try:
        approve_device_by_admin(request_id, admin_user=request.user, second_admin=second_admin)
        messages.success(request, "Admin fallback device approval granted successfully.")
    except (AuthException, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect("/auth/admin/iam/?tab=sessions")
