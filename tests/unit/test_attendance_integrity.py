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


@pytest.mark.django_db
def test_monthly_history_shows_only_own_sessions_in_kolkata_time_with_honest_totals():
    from django.test import Client

    me, other = _u("AT-H1"), _u("AT-H2")
    kol = ZoneInfo("Asia/Kolkata")
    import datetime as dt

    day = dt.datetime(2026, 10, 14, 9, 30, tzinfo=kol)
    at.check_in(me, OK, instant=day)
    at.check_out(me, instant=day + timedelta(hours=8, minutes=15))
    at.check_in(me, OK, instant=day + timedelta(days=1))  # left open
    at.check_in(other, OK, instant=day)
    other_name = f"P {other.employee_id}"
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(me)
    r = c.get("/attendance/history/?month=2026-10")
    html = r.content.decode()
    assert r.status_code == 200 and "October 2026" in html
    assert "09:30 to 17:45" in html and "8.25 h" in html  # Kolkata wall time, not UTC
    assert "still open" in html and "Days with a completed session: 1" in html
    assert "Hours in completed sessions: 8.25" in html
    assert other_name not in html and html.count("cal-row") == 2
    assert (
        "No sessions in this month." in c.get("/attendance/history/?month=2026-09").content.decode()
    )
    for bad in ("2026-13", "26-1", "abc", "2026-10-01", "1999-01"):
        assert c.get("/attendance/history/", {"month": bad}).status_code == 400
    assert Client(HTTP_HOST="app.salowtech.com").get("/attendance/history/").status_code == 302
    assert c.get("/attendance/history/", HTTP_HOST="iam.salowtech.com").status_code == 404


@pytest.mark.django_db
def test_fallback_request_date_must_follow_the_requested_time_and_be_aware():
    from datetime import date

    emp = _u("AT-E3")
    kol = ZoneInfo("Asia/Kolkata")
    import datetime as dt

    late = dt.datetime(2026, 11, 20, 23, 50, tzinfo=kol)  # still the 20th in Kolkata
    ok = at.create_fallback_request(emp, "check_in", date(2026, 11, 20), late, "camera broken")
    assert ok.work_date == date(2026, 11, 20)
    utc_view = late.astimezone(ZoneInfo("UTC"))  # 18:20 UTC, same instant, same Kolkata date
    assert at.create_fallback_request(emp, "check_in", date(2026, 11, 20), utc_view, "x").pk
    with pytest.raises(ValidationError):  # a contradicting date
        at.create_fallback_request(emp, "check_in", date(2026, 11, 21), late, "camera broken")
    with pytest.raises(ValidationError):  # naive time
        at.create_fallback_request(
            emp, "check_in", date(2026, 11, 20), late.replace(tzinfo=None), "x"
        )
    assert AttendanceFallbackRequest.objects.count() == 2


@pytest.mark.django_db
def test_a_refused_approval_writes_no_audit_row_and_an_applied_one_writes_one():
    from apps.core.models import AuditLog as AuditEvent

    emp, mgr = _u("AT-E4"), _u("AT-M4", "manager")
    ReportingRelationship.objects.create(manager=mgr, subordinate=emp)
    now = timezone.now()
    at.check_in(emp, OK, instant=now - timedelta(hours=3))
    req = _fallback(emp, "check_in", now - timedelta(hours=1))
    before = AuditEvent.objects.filter(action="attendance.fallback.approved").count()
    with pytest.raises(ValidationError):
        at.review_fallback_request(req.id, mgr, True)
    assert AuditEvent.objects.filter(action="attendance.fallback.approved").count() == before
    at.check_out(emp, instant=now - timedelta(hours=2))
    at.review_fallback_request(req.id, mgr, True)
    assert AuditEvent.objects.filter(action="attendance.fallback.approved").count() == before + 1


@pytest.mark.django_db
def test_history_names_the_day_when_a_session_runs_past_midnight():
    import datetime as dt

    from django.test import Client

    me = _u("AT-H3")
    kol = ZoneInfo("Asia/Kolkata")
    start = dt.datetime(2026, 10, 14, 23, 30, tzinfo=kol)
    at.check_in(me, OK, instant=start)
    at.check_out(me, instant=start + timedelta(hours=8))  # 07:30 the next day
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(me)
    html = c.get("/attendance/history/?month=2026-10").content.decode()
    assert "23:30 to 07:30 on Thu 15 Oct" in html and "8.0 h" in html


@pytest.mark.django_db
def test_admin_panel_review_messages_name_the_decision_and_unknown_decisions_change_nothing():
    from django.test import Client

    admin, e1, e2, e3 = _u("AT-A9", "admin"), _u("AT-P9"), _u("AT-P8"), _u("AT-P7")
    now = timezone.now()
    r1, r2, r3 = (_fallback(e, "check_in", now) for e in (e1, e2, e3))
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(admin)

    def post(req, decision):
        r = c.post("/attendance/admin/", {"action": "review_fallback", "request_id": req.id, "decision": decision}, follow=True)
        return " ".join(str(m) for m in r.context["messages"])

    assert "Choose Approve or Reject" in post(r3, "foo") and "Choose Approve or Reject" in post(r3, "")
    r3.refresh_from_db()
    assert r3.status == AttendanceFallbackRequest.Status.PENDING  # an unknown decision is not read as a rejection
    assert "successfully approved." in post(r1, "approve")
    assert "successfully rejected." in post(r2, "reject")  # was "rejectd"
    r1.refresh_from_db(), r2.refresh_from_db()
    assert (r1.status, r2.status) == ("approved", "rejected")
