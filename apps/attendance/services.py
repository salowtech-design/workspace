from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.attendance.models import WorkSession
from apps.core.services import record_audit_event
from apps.face.engine.protocols import VerificationResult

KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


def get_current_kolkata_date(dt: datetime) -> datetime.date:
    """Converts a UTC datetime instant to the business work date in Asia/Kolkata."""
    return dt.astimezone(KOLKATA_TZ).date()


def check_in(
    user,
    verification: VerificationResult,
    instant: datetime = None,
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
        # Concurrency guard: select_for_update blocks concurrent check-ins
        existing_open = (
            WorkSession.objects.select_for_update()
            .filter(user=user, state=WorkSession.State.OPEN)
            .first()
        )
        if existing_open:
            raise ValidationError("An open attendance session is already active for this account.")

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
            },
        )

        return session


def check_out(user, instant: datetime = None) -> WorkSession:
    """
    Closes the current active session.
    Enforces check-out strictly after check-in.
    """
    now = instant or timezone.now()

    with transaction.atomic():
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
            },
        )

        return session
