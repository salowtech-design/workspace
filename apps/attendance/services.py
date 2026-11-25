"""
Attendance Service Module.
Encapsulates all state-changing writes for work sessions, manual fallbacks, and corrections.
"""

import math
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.attendance.models import (
    AttendanceCorrection,
    AttendanceFallbackRequest,
    WorkSession,
)
from apps.core.services import record_audit_event
from apps.face.engine.protocols import VerificationResult

KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


def get_current_kolkata_date(dt: datetime) -> datetime.date:
    """Converts a UTC datetime instant to the business work date in Asia/Kolkata."""
    return dt.astimezone(KOLKATA_TZ).date()


def calculate_haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on the earth in meters.
    """
    r = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def is_within_geofence(
    lat: float,
    lon: float,
    office_lat: float,
    office_lon: float,
    radius_meters: float = 200.0,
) -> bool:
    """Determines whether a coordinate falls within the permitted office radius."""
    distance = calculate_haversine_distance(lat, lon, office_lat, office_lon)
    return distance <= radius_meters


def validate_network_and_device(
    ip_address: Optional[str] = None,
    allowed_networks: Optional[list[str]] = None,
    device_id: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Validates employee IP address and device identifier.
    If allowed_networks is configured, ensures the client IP is in allowed subnets.
    """
    if allowed_networks and ip_address:
        import ipaddress

        try:
            client_ip = ipaddress.ip_address(ip_address)
            matched = False
            for net in allowed_networks:
                if "/" in net:
                    if client_ip in ipaddress.ip_network(net, strict=False):
                        matched = True
                        break
                elif str(client_ip) == net:
                    matched = True
                    break
            if not matched:
                return False, f"IP address {ip_address} is outside authorized workplace network."
        except ValueError:
            return False, f"Invalid IP address format: {ip_address}"

    return True, None


def _lock_person(user_pk):
    """The person's own row is the guard: it exists even when they have no open session, so
    concurrent check-ins, check-outs, approvals and corrections for one person queue up here
    (locking only 'the open session' locks nothing when there is none)."""
    return CustomUser.objects.select_for_update().filter(pk=user_pk).first()


def _pause():
    """Test hook after the open-session check. A no-op."""


def check_in(
    user: CustomUser,
    verification: VerificationResult,
    instant: Optional[datetime] = None,
    ip_address: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> WorkSession:
    """
    Enforces check-in rules inside an atomic transaction.
    Guarantees that a user cannot have multiple simultaneous open sessions.
    """
    if not verification.success:
        reason = verification.error_reason or "Biometric identity match failed."
        raise ValidationError(f"Verification failed: {reason}")

    now = instant or timezone.now()
    work_date = get_current_kolkata_date(now)

    with transaction.atomic():
        _lock_person(user.pk)  # the guard row: held until commit
        existing_open = (
            WorkSession.objects.select_for_update()
            .filter(user=user, state=WorkSession.State.OPEN)
            .first()
        )
        if existing_open:
            raise ValidationError("An open attendance session is already active for this account.")
        _pause()
        session = WorkSession.objects.create(
            user=user,
            work_date=work_date,
            check_in_at=now,
            verification_method=verification.method,
            state=WorkSession.State.OPEN,
        )

        record_audit_event(
            action="attendance.check_in.success",
            target_type="work_session",
            target_id=str(session.id),
            actor=user,
            payload_summary={
                "verification_method": verification.method,
                "work_date": str(work_date),
                "ip_address": ip_address,
            },
        )

        return session


def check_out(
    user: CustomUser,
    instant: Optional[datetime] = None,
    ip_address: Optional[str] = None,
) -> WorkSession:
    """
    Closes the current active session.
    Enforces check-out strictly after check-in.
    """
    now = instant or timezone.now()

    with transaction.atomic():
        _lock_person(user.pk)
        session = (
            WorkSession.objects.select_for_update()
            .filter(user=user, state=WorkSession.State.OPEN)
            .first()
        )
        if not session:
            raise ValidationError("No active open attendance session found to check out.")

        if now < session.check_in_at:
            raise ValidationError("Check-out timestamp cannot precede check-in timestamp.")

        session.check_out_at = now
        session.state = WorkSession.State.COMPLETED
        session.save()

        record_audit_event(
            action="attendance.check_out.success",
            target_type="work_session",
            target_id=str(session.id),
            actor=user,
            payload_summary={
                "duration_seconds": (session.check_out_at - session.check_in_at).total_seconds(),
                "ip_address": ip_address,
            },
        )

        return session


def create_fallback_request(
    user: CustomUser,
    request_type: str,
    work_date,
    requested_time: datetime,
    reason: str,
) -> AttendanceFallbackRequest:
    """Create an attendance exception request when biometric verification fails."""
    if request_type not in AttendanceFallbackRequest.RequestType.values:
        raise ValidationError(f"Invalid request type '{request_type}'.")
    if not reason or not reason.strip():
        raise ValidationError("Reason is required for manual attendance fallback request.")

    with transaction.atomic():
        req = AttendanceFallbackRequest.objects.create(
            user=user,
            request_type=request_type,
            work_date=work_date,
            requested_time=requested_time,
            reason=reason.strip(),
            status=AttendanceFallbackRequest.Status.PENDING,
        )
        record_audit_event(
            action="attendance.fallback.requested",
            target_type="attendance_fallback_request",
            target_id=str(req.id),
            actor=user,
            payload_summary={
                "request_type": request_type,
                "work_date": str(work_date),
            },
        )
        return req


def _assert_may_review(reviewer: CustomUser, req: AttendanceFallbackRequest) -> None:
    """No self-review; a Manager may review only their direct reports."""
    if reviewer.id == req.user_id:
        raise PermissionDenied("You cannot review your own attendance request.")
    if reviewer.role == CustomUser.Role.MANAGER:
        from apps.accounts.permissions import can_approve_attendance

        if not can_approve_attendance(reviewer, req.user):
            raise PermissionDenied(
                "Managers can review attendance requests only for their direct reports."
            )


def review_fallback_request(
    request_id: int,
    reviewer: CustomUser,
    approved: bool,
    review_notes: str = "",
) -> AttendanceFallbackRequest:
    """Review and approve or reject a manual attendance request."""
    if reviewer.role not in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.HR,
        CustomUser.Role.MANAGER,
    ):
        raise PermissionDenied(
            "Only managers, HR, or administrators can review attendance fallback requests."
        )

    with transaction.atomic():
        req = AttendanceFallbackRequest.objects.select_for_update().filter(id=request_id).first()
        if not req:
            raise ValidationError("Attendance fallback request not found.")
        if req.status != AttendanceFallbackRequest.Status.PENDING:
            raise ValidationError(f"Request has already been reviewed ({req.status}).")
        _assert_may_review(reviewer, req)

        req.reviewed_by = reviewer
        req.reviewed_at = timezone.now()
        req.review_notes = review_notes.strip()

        if approved:
            req.status = AttendanceFallbackRequest.Status.APPROVED
            req.save()

            _lock_person(req.user_id)  # after the request row; check-in locks only the person
            open_session = (
                WorkSession.objects.select_for_update()
                .filter(user=req.user, state=WorkSession.State.OPEN)
                .first()
            )
            if req.request_type == AttendanceFallbackRequest.RequestType.CHECK_IN:
                if open_session:
                    raise ValidationError(
                        "This person already has an open session, so a manual check-in cannot "
                        "be approved. Nothing was changed."
                    )
                WorkSession.objects.create(
                    user=req.user,
                    work_date=req.work_date,
                    check_in_at=req.requested_time,
                    verification_method=WorkSession.VerificationMethod.MANUAL_APPROVED,
                    state=WorkSession.State.OPEN,
                    notes=f"Approved manual check-in by {reviewer.employee_id}: {req.reason}",
                )
            elif req.request_type == AttendanceFallbackRequest.RequestType.CHECK_OUT:
                if open_session is None:
                    raise ValidationError(
                        "This person has no open session, so a manual check-out cannot be "
                        "approved. Nothing was changed."
                    )
                if req.requested_time < open_session.check_in_at:
                    raise ValidationError(
                        "The requested check-out is before the check-in. Nothing was changed."
                    )
                open_session.check_out_at = req.requested_time
                open_session.state = WorkSession.State.COMPLETED
                open_session.notes = (
                    open_session.notes + f" | Approved manual check-out by {reviewer.employee_id}"
                ).strip(" |")
                open_session.save()
        else:
            req.status = AttendanceFallbackRequest.Status.REJECTED
            req.save()

        record_audit_event(
            action=f"attendance.fallback.{'approved' if approved else 'rejected'}",
            target_type="attendance_fallback_request",
            target_id=str(req.id),
            actor=reviewer,
            payload_summary={
                "target_user": req.user.employee_id,
                "request_type": req.request_type,
            },
        )
        return req


def correct_work_session(
    session_id: int,
    corrected_by: CustomUser,
    corrected_check_in: datetime,
    corrected_check_out: Optional[datetime] = None,
    reason: str = "",
) -> AttendanceCorrection:
    """Audit-tracked correction to completed or open work sessions."""
    if corrected_by.role not in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.HR,
        CustomUser.Role.MANAGER,
    ):
        raise PermissionDenied(
            "Only managers, HR, or administrators can submit attendance corrections."
        )

    if not reason or not reason.strip():
        raise ValidationError("Reason is mandatory for attendance session corrections.")

    if corrected_check_out and corrected_check_out < corrected_check_in:
        raise ValidationError("Corrected check-out cannot precede check-in time.")

    if corrected_check_in.tzinfo is None or (
        corrected_check_out is not None and corrected_check_out.tzinfo is None
    ):
        raise ValidationError("Corrected times must include a time zone.")

    with transaction.atomic():
        found = WorkSession.objects.filter(id=session_id).first()
        if not found:
            raise ValidationError("Work session not found.")
        _lock_person(found.user_id)  # the person's guard row first, then the session
        session = WorkSession.objects.select_for_update().filter(id=session_id).first()
        final_out = corrected_check_out or session.check_out_at
        if final_out is not None and final_out < corrected_check_in:
            raise ValidationError("The corrected check-in would fall after the check-out.")

        correction = AttendanceCorrection.objects.create(
            session=session,
            corrected_by=corrected_by,
            original_check_in=session.check_in_at,
            corrected_check_in=corrected_check_in,
            original_check_out=session.check_out_at,
            corrected_check_out=corrected_check_out,
            reason=reason.strip(),
        )

        session.check_in_at = corrected_check_in
        session.work_date = get_current_kolkata_date(corrected_check_in)
        if corrected_check_out:
            session.check_out_at = corrected_check_out
            session.state = WorkSession.State.COMPLETED
        session.save()

        record_audit_event(
            action="attendance.session.corrected",
            target_type="work_session",
            target_id=str(session.id),
            actor=corrected_by,
            payload_summary={"correction_id": correction.id, "reason": reason.strip()},
        )
        return correction
