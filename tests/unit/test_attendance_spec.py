"""
Unit Tests for Attendance Tracking, Fallback Workflows, Corrections, and Reports.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.attendance.models import (
    AttendanceFallbackRequest,
    WorkSession,
)
from apps.attendance.selectors import (
    get_attendance_report,
    get_open_session,
)
from apps.attendance.services import (
    check_in,
    check_out,
    correct_work_session,
    create_fallback_request,
    is_within_geofence,
    review_fallback_request,
    validate_network_and_device,
)
from apps.face.engine.protocols import VerificationResult

KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def employee(db):
    return CustomUser.objects.create_user(
        employee_id="ST-EMP-3001",
        full_name="Charlie Clock",
        password="ValidStrongPassword123!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.fixture
def manager(db):
    return CustomUser.objects.create_user(
        employee_id="ST-MGR-3001",
        full_name="Manager Mary",
        password="ValidStrongPassword123!",
        role=CustomUser.Role.MANAGER,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.fixture
def success_verification():
    return VerificationResult(
        success=True,
        confidence_score=0.92,
        similarity_score=0.92,
        liveness_score=0.96,
        quality_passed=True,
        method="face",
    )


@pytest.fixture
def failed_verification():
    return VerificationResult(
        success=False,
        confidence_score=0.35,
        similarity_score=0.35,
        liveness_score=0.20,
        quality_passed=False,
        method="face",
        error_reason="Low similarity score.",
    )


@pytest.mark.django_db
class TestClockInOutWorkflow:
    def test_check_in_creates_open_session_with_kolkata_date(self, employee, success_verification):
        """Successful verification opens work session with Asia/Kolkata work date."""
        now = timezone.now()
        expected_date = now.astimezone(KOLKATA_TZ).date()

        session = check_in(user=employee, verification=success_verification, instant=now)
        assert session.user == employee
        assert session.state == WorkSession.State.OPEN
        assert session.work_date == expected_date
        assert session.check_in_at == now
        assert session.check_out_at is None

    def test_check_in_fails_with_unsuccessful_verification(self, employee, failed_verification):
        """Failed biometric verification blocks session creation."""
        with pytest.raises(ValidationError, match="Verification failed: Low similarity score"):
            check_in(user=employee, verification=failed_verification)

    def test_cannot_have_multiple_open_sessions(self, employee, success_verification):
        """User cannot create a second open session while one is active."""
        check_in(user=employee, verification=success_verification)
        with pytest.raises(ValidationError, match="open attendance session is already active"):
            check_in(user=employee, verification=success_verification)

    def test_check_out_completes_session(self, employee, success_verification):
        """Check-out records end time and marks session completed."""
        in_time = timezone.now()
        check_in(user=employee, verification=success_verification, instant=in_time)

        out_time = in_time + timedelta(hours=8)
        session = check_out(user=employee, instant=out_time)
        assert session.state == WorkSession.State.COMPLETED
        assert session.check_out_at == out_time

    def test_check_out_without_active_session_raises(self, employee):
        """Check-out when no session is open raises ValidationError."""
        with pytest.raises(ValidationError, match="No active open attendance session found"):
            check_out(user=employee)

    def test_check_out_before_check_in_rejected(self, employee, success_verification):
        """Check-out timestamp cannot precede check-in."""
        in_time = timezone.now()
        check_in(user=employee, verification=success_verification, instant=in_time)

        past_time = in_time - timedelta(minutes=10)
        with pytest.raises(ValidationError, match="cannot precede check-in"):
            check_out(user=employee, instant=past_time)


@pytest.mark.django_db
class TestManualFallbackWorkflow:
    def test_create_fallback_request(self, employee):
        """Employee submits manual fallback request when biometric fails."""
        now = timezone.now()
        work_date = now.astimezone(KOLKATA_TZ).date()
        req = create_fallback_request(
            user=employee,
            request_type=AttendanceFallbackRequest.RequestType.CHECK_IN,
            work_date=work_date,
            requested_time=now,
            reason="Camera driver glitch on office workstation.",
        )
        assert req.status == AttendanceFallbackRequest.Status.PENDING
        assert req.user == employee

    def test_fallback_request_requires_reason(self, employee):
        """Empty reason is rejected."""
        with pytest.raises(ValidationError, match="Reason is required"):
            create_fallback_request(
                user=employee,
                request_type=AttendanceFallbackRequest.RequestType.CHECK_IN,
                work_date=timezone.now().date(),
                requested_time=timezone.now(),
                reason="",
            )

    def test_manager_approves_fallback_check_in(self, employee, manager):
        """Manager approves fallback check-in request; opens work session with MANUAL_APPROVED."""
        now = timezone.now()
        work_date = now.astimezone(KOLKATA_TZ).date()
        req = create_fallback_request(
            user=employee,
            request_type=AttendanceFallbackRequest.RequestType.CHECK_IN,
            work_date=work_date,
            requested_time=now,
            reason="Hardware replacement in progress.",
        )

        reviewed = review_fallback_request(
            request_id=req.id,
            reviewer=manager,
            approved=True,
            review_notes="Confirmed hardware failure.",
        )
        assert reviewed.status == AttendanceFallbackRequest.Status.APPROVED
        assert reviewed.reviewed_by == manager

        # Session should now exist
        session = get_open_session(employee)
        assert session is not None
        assert session.verification_method == WorkSession.VerificationMethod.MANUAL_APPROVED

    def test_employee_cannot_review_own_request(self, employee):
        """Non-manager cannot review fallback request."""
        req = create_fallback_request(
            user=employee,
            request_type=AttendanceFallbackRequest.RequestType.CHECK_IN,
            work_date=timezone.now().date(),
            requested_time=timezone.now(),
            reason="Test reason",
        )
        with pytest.raises(PermissionDenied):
            review_fallback_request(request_id=req.id, reviewer=employee, approved=True)

    def test_manager_rejects_fallback_request(self, employee, manager):
        """Manager rejects fallback request; no session created."""
        req = create_fallback_request(
            user=employee,
            request_type=AttendanceFallbackRequest.RequestType.CHECK_IN,
            work_date=timezone.now().date(),
            requested_time=timezone.now(),
            reason="Unsubstantiated request",
        )
        reviewed = review_fallback_request(
            request_id=req.id, reviewer=manager, approved=False, review_notes="Invalid"
        )
        assert reviewed.status == AttendanceFallbackRequest.Status.REJECTED
        assert get_open_session(employee) is None


@pytest.mark.django_db
class TestAttendanceCorrections:
    def test_manager_corrects_session_with_audit_trail(
        self, employee, manager, success_verification
    ):
        """Manager corrects timestamps, preserving original timestamps in correction record."""
        orig_in = timezone.now() - timedelta(hours=9)
        orig_out = orig_in + timedelta(hours=7)

        session = check_in(user=employee, verification=success_verification, instant=orig_in)
        check_out(user=employee, instant=orig_out)

        corrected_in = orig_in - timedelta(minutes=30)
        corrected_out = orig_out + timedelta(minutes=30)

        correction = correct_work_session(
            session_id=session.id,
            corrected_by=manager,
            corrected_check_in=corrected_in,
            corrected_check_out=corrected_out,
            reason="Employee worked overtime at client site.",
        )

        assert correction.session == session
        assert correction.original_check_in == orig_in
        assert correction.original_check_out == orig_out
        assert correction.corrected_check_in == corrected_in
        assert correction.corrected_check_out == corrected_out

        session.refresh_from_db()
        assert session.check_in_at == corrected_in
        assert session.check_out_at == corrected_out


class TestGeolocationAndNetworkValidation:
    def test_geofence_haversine_calculation(self):
        """Validates geofencing distance within office bounds."""
        office_lat, office_lon = 12.9716, 77.5946  # Bengaluru center
        # Point ~50 meters away
        nearby_lat, nearby_lon = 12.9718, 77.5948
        # Point ~2 km away
        far_lat, far_lon = 12.9900, 77.6100

        assert is_within_geofence(
            nearby_lat, nearby_lon, office_lat, office_lon, radius_meters=100.0
        )
        assert not is_within_geofence(far_lat, far_lon, office_lat, office_lon, radius_meters=100.0)

    def test_network_subnet_validation(self):
        """Validates authorized workplace subnet boundaries."""
        allowed = ["192.168.1.0/24", "10.0.0.1"]

        valid_ip = "192.168.1.55"
        exact_ip = "10.0.0.1"
        external_ip = "203.0.113.19"

        ok, err = validate_network_and_device(ip_address=valid_ip, allowed_networks=allowed)
        assert ok is True and err is None

        ok, err = validate_network_and_device(ip_address=exact_ip, allowed_networks=allowed)
        assert ok is True and err is None

        ok, err = validate_network_and_device(ip_address=external_ip, allowed_networks=allowed)
        assert ok is False
        assert "outside authorized workplace network" in err


@pytest.mark.django_db
class TestAttendanceReporting:
    def test_attendance_report_aggregation(self, employee, success_verification):
        """Attendance report aggregates total sessions, completed sessions, and hours."""
        base_time = timezone.now() - timedelta(days=2)

        # Session 1: 8 hours
        check_in(user=employee, verification=success_verification, instant=base_time)
        check_out(user=employee, instant=base_time + timedelta(hours=8))

        # Session 2: 4 hours
        check_in(
            user=employee,
            verification=success_verification,
            instant=base_time + timedelta(days=1),
        )
        check_out(user=employee, instant=base_time + timedelta(days=1, hours=4))

        report = get_attendance_report(
            start_date=(base_time - timedelta(days=1)).date(),
            end_date=timezone.now().date(),
            user=employee,
        )

        assert report["total_sessions"] == 2
        assert report["completed_sessions"] == 2
        assert report["open_sessions"] == 0
        assert report["total_hours_worked"] == 12.0
