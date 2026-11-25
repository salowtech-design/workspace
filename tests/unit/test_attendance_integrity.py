import threading
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.attendance import services as at
from apps.attendance.models import AttendanceFallbackRequest, WorkSession
from apps.directory.models import ReportingRelationship
from apps.face.engine.protocols import VerificationResult

OK = VerificationResult(
    success=True, confidence_score=0.9, similarity_score=0.9, liveness_score=0.9,
    quality_passed=True, method="face",
)  # fmt: skip


def _u(eid, role="employee"):
    return CustomUser.objects.create_user(
        employee_id=eid, full_name=f"P {eid}", password="ValidStrongPassword123!",
        role=role, status="active",
    )  # fmt: skip


def _plain_lock(user_pk):
    return CustomUser.objects.filter(pk=user_pk).first()  # the control: no guard row


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_two_check_ins_make_one_open_session(monkeypatch, locked):
    from django.db import connection

    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("concurrent writers are only meaningful on the TiDB/MySQL backend")
    if not locked:
        monkeypatch.setattr(at, "_lock_person", _plain_lock)
    p = _u(f"AT-RACE-{locked}")
    barrier = threading.Barrier(2)

    def pause():
        try:
            barrier.wait(timeout=1.5 if locked else 10)  # both saw "no open session"
        except threading.BrokenBarrierError:
            pass  # locked: the other check-in is held at the guard row

    monkeypatch.setattr(at, "_pause", pause)
    out = _threads([lambda: at.check_in(p, OK), lambda: at.check_in(p, OK)])
    open_n = WorkSession.objects.filter(user=p, state="open").count()
    if locked:
        assert sorted(out) == ["ok", "refused:ValidationError"] and open_n == 1, (out, open_n)
    else:
        assert out == ["ok", "ok"] and open_n == 2, (out, open_n)  # the anomaly: two open sessions


def _fallback(employee, kind, when):
    return AttendanceFallbackRequest.objects.create(
        user=employee, request_type=kind, reason="camera broken",
        work_date=at.get_current_kolkata_date(when), requested_time=when,
    )  # fmt: skip


@pytest.mark.django_db
def test_approvals_that_could_not_take_effect_are_refused_not_silently_approved():
    emp, mgr = _u("AT-E1"), _u("AT-M1", "manager")
    ReportingRelationship.objects.create(manager=mgr, subordinate=emp)
    now = timezone.now()
    s = at.check_in(emp, OK, instant=now - timedelta(hours=3))
    ci = _fallback(emp, "check_in", now - timedelta(hours=1))
    with pytest.raises(ValidationError):
        at.review_fallback_request(ci.id, mgr, True)  # already has an open session
    ci.refresh_from_db()
    assert ci.status == "pending" and WorkSession.objects.filter(user=emp).count() == 1
    early = _fallback(emp, "check_out", now - timedelta(hours=5))  # before the check-in
    with pytest.raises(ValidationError):
        at.review_fallback_request(early.id, mgr, True)
    s.refresh_from_db()
    assert s.state == "open" and s.check_out_at is None
    at.check_out(emp, instant=now - timedelta(hours=2))
    none = _fallback(emp, "check_out", now - timedelta(hours=1, minutes=30))
    with pytest.raises(ValidationError):
        at.review_fallback_request(none.id, mgr, True)  # nothing left to close
    none.refresh_from_db()
    assert none.status == "pending"
    at.review_fallback_request(none.id, mgr, False)  # rejecting is still allowed
    none.refresh_from_db()
    assert none.status == "rejected"


@pytest.mark.django_db
def test_correction_keeps_the_record_consistent():
    emp, mgr = _u("AT-E2"), _u("AT-M2", "manager")
    base = timezone.now().replace(microsecond=0) - timedelta(days=2)
    s = at.check_in(emp, OK, instant=base)
    at.check_out(emp, instant=base + timedelta(hours=8))
    with pytest.raises(ValidationError):  # new check-in after the existing check-out
        at.correct_work_session(s.id, mgr, base + timedelta(hours=9), None, "typo")
    with pytest.raises(ValidationError):  # naive times
        at.correct_work_session(s.id, mgr, base.replace(tzinfo=None), None, "typo")
    s.refresh_from_db()
    assert s.check_in_at == base
    # moving the check-in across midnight Kolkata moves the business date with it
    kol = ZoneInfo("Asia/Kolkata")
    late = base.astimezone(kol).replace(hour=0, minute=30)
    prev = late - timedelta(hours=2)  # 22:30 the day before
    at.correct_work_session(s.id, mgr, prev, None, "started the night before")
    s.refresh_from_db()
    assert s.work_date == at.get_current_kolkata_date(prev) and s.check_in_at == prev
