"""
Unit & Integration Tests for Second-Device Verification — Salow Workspace IAM.
Validates:
1. First device logs in directly without device approval challenge.
2. Second device initiates pending challenge with strictly 1-minute expiration.
3. In-app approval from Device A dispatches 6-digit email OTP.
4. Email OTP verification and 1:1 facial verification gates.
5. Independent concurrent sessions for both devices (up to 2).
6. Administrator fallback approval with dual-control for Super Admin targets.
7. Anti-replay and rejection handling.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils import timezone

from apps.accounts.auth.device_approval import (
    DEVICE_APPROVAL_EXPIRY_SECONDS,
    approve_device_by_admin,
    approve_device_by_existing_session,
    check_and_expire_request,
    complete_device_verification_and_login,
    create_device_approval_request,
    is_second_device_request,
    reject_device_request,
    verify_device_email_otp,
)
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.sessions import create_verified_session
from apps.accounts.models import CustomUser, DeviceApprovalRequest, UserSession
from apps.core.models import EmailOutbox

User = get_user_model()


@pytest.fixture
def dual_device_user(db):
    user = User.objects.create(
        employee_id="ST-DEV-0001",
        full_name="Device Test Employee",
        email="dev.employee@salowtech.com",
        email_verified=True,
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("SecurePassword2026!@#")
    user.save()
    return user


@pytest.fixture
def super_admin_user(db):
    user = User.objects.create(
        employee_id="ST-SUP-0001",
        full_name="Super Administrator",
        email="super.admin@salowtech.com",
        email_verified=True,
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_staff=True,
        is_superuser=True,
    )
    user.set_password("SuperAdminPass2026!@#")
    user.save()
    return user


@pytest.fixture
def admin_user(db):
    user = User.objects.create(
        employee_id="ST-ADM-0001",
        full_name="Standard Administrator",
        email="admin@salowtech.com",
        email_verified=True,
        role=CustomUser.Role.ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_staff=True,
    )
    user.set_password("AdminPass2026!@#")
    user.save()
    return user


@pytest.mark.django_db
class TestSecondDeviceVerification:
    def test_first_device_has_no_prior_sessions(self, dual_device_user):
        """Initial login does not detect secondary device."""
        assert is_second_device_request(dual_device_user) is False

    def test_second_device_detected_when_session_active(self, rf, dual_device_user):
        """Active session on Device A causes second device to be detected."""
        request = rf.get("/")
        request.session = Client().session
        create_verified_session(request, dual_device_user)

        assert UserSession.objects.filter(user=dual_device_user, mfa_verified=True).count() == 1
        assert is_second_device_request(dual_device_user) is True

    def test_create_device_approval_request_strict_one_minute_expiry(self, dual_device_user):
        """Pending challenge has strictly 1-minute expiration window."""
        req = create_device_approval_request(
            user=dual_device_user,
            ip_address="192.168.1.100",
            user_agent="Mozilla/5.0 TestBrowser",
        )
        assert req.status == DeviceApprovalRequest.Status.PENDING
        assert req.requesting_device_ip == "192.168.1.100"

        diff_seconds = (req.expires_at - req.created_at).total_seconds()
        assert abs(diff_seconds - DEVICE_APPROVAL_EXPIRY_SECONDS) < 2

    def test_request_expires_after_one_minute(self, dual_device_user):
        """Request transitions to EXPIRED when past deadline."""
        req = create_device_approval_request(user=dual_device_user)
        # Advance clock past expiry
        req.expires_at = timezone.now() - timedelta(seconds=5)
        req.save()

        check_and_expire_request(req)
        req.refresh_from_db()
        assert req.status == DeviceApprovalRequest.Status.EXPIRED

    def test_primary_device_approval_flow_and_email_otp(self, dual_device_user):
        """Device A approves request with matching number; single-use OTP is sent to company email."""
        req = create_device_approval_request(user=dual_device_user)

        approved_req = approve_device_by_existing_session(
            req.request_id, approver=dual_device_user, submitted_number=req.number_challenge
        )
        assert approved_req.status == DeviceApprovalRequest.Status.APPROVED
        assert approved_req.approved_by == dual_device_user
        assert approved_req.email_otp_hash != ""

        # Verify email was enqueued
        email = EmailOutbox.objects.filter(recipient=dual_device_user.email).first()
        assert email is not None
        assert "Second Device Verification OTP" in email.subject

    def test_number_matching_enforcement_and_rejections(self, dual_device_user):
        """Number matching is strictly mandatory; missing or mismatched numbers are rejected."""
        req = create_device_approval_request(user=dual_device_user)

        # 1. Missing challenge number rejected
        with pytest.raises(AuthException) as exc1:
            approve_device_by_existing_session(req.request_id, approver=dual_device_user, submitted_number=None)
        assert "mandatory" in str(exc1.value).lower()

        # 2. Mismatched challenge number rejected
        wrong_number = (req.number_challenge + 1) if req.number_challenge < 99 else 10
        with pytest.raises(AuthException) as exc2:
            approve_device_by_existing_session(req.request_id, approver=dual_device_user, submitted_number=wrong_number)
        assert "mismatch" in str(exc2.value).lower()

        # 3. Matching challenge number accepted
        approved = approve_device_by_existing_session(
            req.request_id, approver=dual_device_user, submitted_number=req.number_challenge
        )
        assert approved.status == DeviceApprovalRequest.Status.APPROVED

    def test_device_a_htmx_polling_renders_modal(self, rf, dual_device_user):
        """Device A's HTMX polling endpoint renders approval modal with matching number when pending."""
        from apps.accounts.views.device_approval import check_pending_device_approval_view

        request = rf.get("/auth/devices/approval/pending-check/")
        request.user = dual_device_user

        # No pending request: returns empty
        resp1 = check_pending_device_approval_view(request)
        assert resp1.status_code == 200
        assert resp1.content.decode("utf-8") == ""

        # Pending request created: returns modal with number challenge
        req = create_device_approval_request(user=dual_device_user)
        resp2 = check_pending_device_approval_view(request)
        assert resp2.status_code == 200
        html = resp2.content.decode("utf-8")
        assert "Secondary Device Access Request" in html
        assert str(req.number_challenge) in html
        assert "id_matching_number" in html

    def test_different_user_cannot_approve_device_request(self, dual_device_user, admin_user):
        """Arbitrary user cannot approve device request belonging to another employee."""
        req = create_device_approval_request(user=dual_device_user)
        with pytest.raises(PermissionDenied):
            approve_device_by_existing_session(req.request_id, approver=admin_user, submitted_number=req.number_challenge)

    def test_secondary_device_email_otp_verification(self, dual_device_user):
        """Device B submits the 6-digit email OTP; successfully validates against hash."""
        req = create_device_approval_request(user=dual_device_user)
        approve_device_by_existing_session(req.request_id, approver=dual_device_user, submitted_number=req.number_challenge)

        # Retrieve raw OTP from queued email
        email = EmailOutbox.objects.filter(recipient=dual_device_user.email).latest("created_at")
        import re
        match = re.search(r"code is: (\d{6})", email.body_text)
        assert match is not None
        raw_otp = match.group(1)

        # Invalid OTP rejected
        assert verify_device_email_otp(req.request_id, "999999") is False

        # Valid OTP accepted
        assert verify_device_email_otp(req.request_id, raw_otp) is True
        req.refresh_from_db()
        assert req.email_otp_verified is True

    def test_complete_device_verification_and_session_creation(self, rf, dual_device_user):
        """After email OTP and biometric face check, independent session is granted."""
        req = create_device_approval_request(user=dual_device_user)
        approve_device_by_existing_session(req.request_id, approver=dual_device_user, submitted_number=req.number_challenge)

        email = EmailOutbox.objects.filter(recipient=dual_device_user.email).latest("created_at")
        import re
        raw_otp = re.search(r"code is: (\d{6})", email.body_text).group(1)
        verify_device_email_otp(req.request_id, raw_otp)

        # Execute final login on Device B
        request_b = rf.get("/")
        request_b.session = Client().session

        user_out = complete_device_verification_and_login(request_b, req.request_id, face_verified=True)
        assert user_out == dual_device_user
        assert request_b.session.get("_salow_mfa_verified") is True

        req.refresh_from_db()
        assert req.face_verified is True
        assert req.completed_at is not None

    def test_rejection_blocks_secondary_device(self, dual_device_user):
        """Rejecting device request transitions status to REJECTED."""
        req = create_device_approval_request(user=dual_device_user)
        reject_device_request(req.request_id, actor=dual_device_user, reason="Unauthorized device detected")

        req.refresh_from_db()
        assert req.status == DeviceApprovalRequest.Status.REJECTED
        assert "Unauthorized device" in req.rejection_reason

    def test_admin_fallback_requires_dual_control_for_super_admin(
        self, super_admin_user, admin_user
    ):
        """Admin fallback on Super Admin account requires second administrator confirmation."""
        req = create_device_approval_request(user=super_admin_user)

        # Single admin approval rejected
        with pytest.raises(AuthException) as exc:
            approve_device_by_admin(req.request_id, admin_user=admin_user, second_admin=None)
        assert "dual administrator approval" in str(exc.value)

        # Dual admin approval accepted
        second_admin = User.objects.create(
            employee_id="ST-ADM-0002",
            full_name="Second Admin",
            email="admin2@salowtech.com",
            role=CustomUser.Role.ADMIN,
            status=CustomUser.Status.ACTIVE,
        )
        approved_req = approve_device_by_admin(
            req.request_id, admin_user=admin_user, second_admin=second_admin
        )
        assert approved_req.status == DeviceApprovalRequest.Status.APPROVED
        assert approved_req.approval_type == "admin_fallback"

    def test_admin_fallback_self_approval_prohibited(self, admin_user):
        """Administrator cannot approve their own fallback device request (no self-approval)."""
        req = create_device_approval_request(user=admin_user)
        with pytest.raises(PermissionDenied) as exc:
            approve_device_by_admin(req.request_id, admin_user=admin_user)
        assert "Self-approval is strictly prohibited" in str(exc.value)

    def test_expired_email_otp_rejected(self, dual_device_user):
        """Email OTP submitted after 5 minutes is rejected."""
        req = create_device_approval_request(user=dual_device_user)
        approve_device_by_existing_session(req.request_id, approver=dual_device_user, submitted_number=req.number_challenge)

        email = EmailOutbox.objects.filter(recipient=dual_device_user.email).latest("created_at")
        import re
        raw_otp = re.search(r"code is: (\d{6})", email.body_text).group(1)

        # Fast forward approved_at past 5 minutes
        req.approved_at = timezone.now() - timedelta(minutes=6)
        req.save(update_fields=["approved_at"])

        assert verify_device_email_otp(req.request_id, raw_otp) is False

    def test_replaying_completed_device_request_rejected(self, rf, dual_device_user):
        """Completed approval request cannot be reused or re-verified (anti-replay)."""
        req = create_device_approval_request(user=dual_device_user)
        approve_device_by_existing_session(req.request_id, approver=dual_device_user, submitted_number=req.number_challenge)

        email = EmailOutbox.objects.filter(recipient=dual_device_user.email).latest("created_at")
        import re
        raw_otp = re.search(r"code is: (\d{6})", email.body_text).group(1)
        assert verify_device_email_otp(req.request_id, raw_otp) is True

        request_b = rf.get("/")
        request_b.session = Client().session
        complete_device_verification_and_login(request_b, req.request_id, face_verified=True)

        # Replay attempt 1: verify OTP again on completed request
        assert verify_device_email_otp(req.request_id, raw_otp) is False

        # Replay attempt 2: create session again on completed request
        request_c = rf.get("/")
        request_c.session = Client().session
        with pytest.raises(AuthException) as exc:
            complete_device_verification_and_login(request_c, req.request_id, face_verified=True)
        assert "already been completed" in str(exc.value)

    def test_unverified_face_rejected(self, rf, dual_device_user):
        """Attempting to complete device login without verified face raises AuthException."""
        req = create_device_approval_request(user=dual_device_user)
        approve_device_by_existing_session(req.request_id, approver=dual_device_user, submitted_number=req.number_challenge)

        email = EmailOutbox.objects.filter(recipient=dual_device_user.email).latest("created_at")
        import re
        raw_otp = re.search(r"code is: (\d{6})", email.body_text).group(1)
        verify_device_email_otp(req.request_id, raw_otp)

        request_b = rf.get("/")
        request_b.session = Client().session
        with pytest.raises(AuthException) as exc:
            complete_device_verification_and_login(request_b, req.request_id, face_verified=False)
        assert "Biometric face verification failed" in str(exc.value)
