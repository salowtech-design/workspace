# ruff: noqa: B023  (the race lambdas run and finish inside the loop pass that defines them)
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from apps.accounts.models import CustomUser
from apps.core.models import AuditLog, Notification
from apps.tasks import services as svc
from apps.tasks.models import Task


def _u(eid, status="active"):
    return CustomUser.objects.create(
        employee_id=eid, full_name=eid, email=f"{eid.lower()}@salowtech.com",
        role="employee", status=status, is_active=True,
    )  # fmt: skip


@pytest.fixture
def setup(db):
    a, b, c = _u("T-A"), _u("T-B"), _u("T-C")
    return a, b, c, svc.create_list(a, "Work")


def test_create_validates_and_orders(setup):
    a, b, c, tl = setup
    with pytest.raises(ValidationError):
        svc.create_task(a, tl, "   ")
    with pytest.raises(ValidationError):
        svc.create_task(a, tl, "x" * 1025)
    with pytest.raises(ValidationError):
        svc.create_task(a, tl, "ok", notes="n" * 8193)
    t1, t2 = svc.create_task(a, tl, "one"), svc.create_task(a, tl, "two")
    assert [t.title for t in svc.tasks_in(a, tl)] == [
        "one",
        "two",
    ] and t2.position == t1.position + 1


def test_only_the_list_owner_can_add_or_view(setup):
    a, b, c, tl = setup
    with pytest.raises(PermissionDenied):
        svc.create_task(b, tl, "intruder")
    with pytest.raises(PermissionDenied):
        svc.tasks_in(b, tl)


def test_subtasks_one_level_same_list_not_under_assigned(setup):
    a, b, c, tl = setup
    top = svc.create_task(a, tl, "top")
    sub = svc.create_task(a, tl, "sub", parent=top)
    with pytest.raises(ValidationError):
        svc.create_task(a, tl, "deep", parent=sub)
    other = svc.create_list(a, "Other")
    with pytest.raises(ValidationError):
        svc.create_task(a, other, "cross", parent=top)
    given = svc.create_task(a, tl, "given")
    svc.assign(a, given, b)
    with pytest.raises(ValidationError):
        svc.create_task(a, tl, "under given", parent=given)
    with pytest.raises(ValidationError):
        svc.assign(a, top, b)  # has a subtask


def test_update_is_compare_and_set(setup):
    a, b, c, tl = setup
    t = svc.create_task(a, tl, "v1")
    svc.update_task(a, t, 1, title="v2")
    with pytest.raises(ValidationError):
        svc.update_task(a, t, 1, title="stale")  # version moved on
    t.refresh_from_db()
    assert t.title == "v2" and t.version == 2


def test_assignee_can_complete_but_not_edit_or_delete(setup):
    a, b, c, tl = setup
    t = svc.create_task(a, tl, "do it")
    svc.assign(a, t, b)
    assert Notification.objects.filter(user=b, kind="tasks.assigned").count() == 1
    assert [x.pk for x in svc.assigned_to(b)] == [t.pk]
    svc.set_status(b, t, True)
    t.refresh_from_db()
    assert t.status == "completed" and t.completed_at
    with pytest.raises(PermissionDenied):
        svc.update_task(b, t, t.version, title="mine now")
    with pytest.raises(PermissionDenied):
        svc.delete_task(b, t)
    with pytest.raises(PermissionDenied):
        svc.assign(b, t, c)
    with pytest.raises(PermissionDenied):
        svc.set_status(c, t, False)  # a third person has no access


def test_unassign_removes_access_and_inactive_person_refused(setup):
    a, b, c, tl = setup
    t = svc.create_task(a, tl, "x")
    svc.assign(a, t, b)
    svc.assign(a, t, None)
    assert list(svc.assigned_to(b)) == []
    with pytest.raises(Http404):
        svc.get_task(b, t.public_id)
    off = _u("T-OFF", status="suspended")
    with pytest.raises(ValidationError):
        svc.assign(a, t, off)


def test_completing_a_parent_completes_open_subtasks_reopen_does_not(setup):
    a, b, c, tl = setup
    top = svc.create_task(a, tl, "top")
    s1, s2 = svc.create_task(a, tl, "s1", parent=top), svc.create_task(a, tl, "s2", parent=top)
    svc.set_status(a, top, True)
    assert set(Task.objects.filter(parent=top).values_list("status", flat=True)) == {"completed"}
    svc.set_status(a, top, False)
    assert set(Task.objects.filter(parent=top).values_list("status", flat=True)) == {"completed"}
    assert s1.pk and s2.pk


def test_delete_hides_task_subtasks_and_assignee_access(setup):
    a, b, c, tl = setup
    top = svc.create_task(a, tl, "top")
    sub = svc.create_task(a, tl, "sub", parent=top)
    giv = svc.create_task(a, tl, "giv")
    svc.assign(a, giv, b)
    svc.delete_task(a, top)
    assert not Task.objects.filter(pk__in=[top.pk, sub.pk], deleted_at__isnull=True).exists()
    svc.delete_list(a, tl)
    assert list(svc.assigned_to(b)) == []
    with pytest.raises(Http404):
        svc.get_task(a, giv.public_id)


def test_notification_failure_never_undoes_assignment_but_db_error_does(setup, monkeypatch):
    from django.db import OperationalError

    a, b, c, tl = setup
    t = svc.create_task(a, tl, "x")

    def boom(*args, **kwargs):
        raise RuntimeError("notify down")

    monkeypatch.setattr(svc, "notify", boom)
    svc.assign(a, t, b)
    t.refresh_from_db()
    assert t.assignee_id == b.pk

    def db_down(*args, **kwargs):
        raise OperationalError(1213, "Deadlock found")

    monkeypatch.setattr(svc, "notify", db_down)
    with pytest.raises(OperationalError):
        svc.assign(a, t, c)


def test_audit_rows_carry_no_titles_or_notes(setup):
    a, b, c, tl = setup
    svc.create_task(a, tl, "secret plan", notes="private notes")
    blob = " ".join(str(r) for r in AuditLog.objects.filter(action__startswith="tasks.").values())
    assert "secret plan" not in blob and "private notes" not in blob


def test_stale_assignee_is_refused_after_unassign(setup):
    a, b, c, tl = setup
    t = svc.create_task(a, tl, "x")
    svc.assign(a, t, b)
    stale = svc.get_task(b, t.public_id, "complete")  # b fetched it while still assigned
    svc.assign(a, t, None)
    with pytest.raises(PermissionDenied):
        svc.set_status(b, stale, True)
    t.refresh_from_db()
    assert t.status == "open"


def test_repeat_calls_are_no_ops(setup):
    a, b, c, tl = setup
    t = svc.create_task(a, tl, "x")
    svc.assign(a, t, b)
    svc.assign(a, t, b)  # same person again: no second notice, no version bump
    assert Notification.objects.filter(user=b, kind="tasks.assigned").count() == 1
    svc.set_status(a, t, True)
    t.refresh_from_db()
    v, done_at = t.version, t.completed_at
    svc.set_status(a, t, True)
    t.refresh_from_db()
    assert t.version == v and t.completed_at == done_at
    assert AuditLog.objects.filter(action="tasks.task.complete").count() == 1


def test_strict_fields_and_dates(setup):
    import datetime

    a, b, c, tl = setup
    t = svc.create_task(a, tl, "x")
    with pytest.raises(ValidationError):
        svc.update_task(a, t, 1, status="completed")
    with pytest.raises(ValidationError):
        svc.update_task(a, t, 1, due_date="2026-12-31")
    with pytest.raises(ValidationError):
        svc.update_task(a, t, 1, due_date=datetime.datetime(2026, 12, 31, 10, 0))
    svc.update_task(a, t, 1, due_date=datetime.date(2026, 12, 31))
    t.refresh_from_db()
    assert t.due_date == datetime.date(2026, 12, 31) and t.status == "open"


def test_completed_parent_never_holds_an_open_subtask_and_audit_counts(setup):
    a, b, c, tl = setup
    top = svc.create_task(a, tl, "top")
    s1, s2 = svc.create_task(a, tl, "s1", parent=top), svc.create_task(a, tl, "s2", parent=top)
    svc.set_status(a, top, True)
    row = AuditLog.objects.get(action="tasks.task.complete")
    assert row.payload_summary.get("subtasks_changed") == 2
    svc.set_status(a, s1, False)  # reopening a child reopens the parent
    top.refresh_from_db()
    assert top.status == "open"
    svc.set_status(a, top, True)
    svc.create_task(a, tl, "s3", parent=top)  # a new child under a completed parent reopens it
    top.refresh_from_db()
    assert top.status == "open" and s2.pk


def test_delete_audit_counts(setup):
    a, b, c, tl = setup
    top = svc.create_task(a, tl, "top")
    svc.create_task(a, tl, "s1", parent=top)
    svc.delete_task(a, top)
    assert AuditLog.objects.get(action="tasks.task.delete").payload_summary["subtasks_deleted"] == 1
    svc.create_task(a, tl, "again")
    svc.delete_list(a, tl)
    assert AuditLog.objects.get(action="tasks.list.delete").payload_summary["tasks_deleted"] == 1


# ---- concurrency (TiDB/MySQL only). Each race has a control with the list lock removed ----


def _need_mysql():
    from django.db import connection

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")


def _race(monkeypatch, locked, parties, fns):
    import threading

    from apps.tasks.models import TaskList
    from tests.unit.test_drive_services import _threads

    barrier = threading.Barrier(parties)

    def pause():
        try:
            barrier.wait(timeout=1.5)
        except threading.BrokenBarrierError:
            pass  # the lock kept the other side out, as it should

    monkeypatch.setattr(svc, "_pause", pause)
    if not locked:
        monkeypatch.setattr(svc, "_lock_list", lambda tl: TaskList.objects.get(pk=tl.pk))
    return _threads(fns)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_assign_vs_new_subtask(monkeypatch, locked):
    _need_mysql()
    broken = 0
    for i in range(2 if locked else 3):
        a, b = _u(f"R1-A{i}{locked}"), _u(f"R1-B{i}{locked}")
        tl = svc.create_list(a, "L")
        top = svc.create_task(a, tl, "top")
        _race(
            monkeypatch,
            locked,
            2,
            [lambda: svc.assign(a, top, b), lambda: svc.create_task(a, tl, "kid", parent=top)],
        )
        top.refresh_from_db()
        if top.assignee_id and top.subtasks.filter(deleted_at__isnull=True).exists():
            broken += 1
    assert (broken == 0) if locked else (broken > 0), broken


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_complete_parent_vs_new_subtask(monkeypatch, locked):
    _need_mysql()
    broken = 0
    for i in range(2 if locked else 3):
        a = _u(f"R2-A{i}{locked}")
        tl = svc.create_list(a, "L")
        top = svc.create_task(a, tl, "top")
        _race(
            monkeypatch,
            locked,
            2,
            [
                lambda: svc.set_status(a, top, True),
                lambda: svc.create_task(a, tl, "kid", parent=top),
            ],
        )
        top.refresh_from_db()
        if (
            top.status == "completed"
            and top.subtasks.filter(deleted_at__isnull=True, status="open").exists()
        ):
            broken += 1
    assert (broken == 0) if locked else (broken > 0), broken


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_positions_and_list_delete(monkeypatch, locked):
    from apps.tasks.models import Task

    _need_mysql()
    a = _u(f"R3-A{locked}")
    tl = svc.create_list(a, "L")
    _race(
        monkeypatch,
        locked,
        3,
        [
            lambda: svc.create_task(a, tl, "t"),
            lambda: svc.create_task(a, tl, "t"),
            lambda: svc.create_task(a, tl, "t"),
        ],
    )
    pos = list(Task.objects.filter(task_list=tl).values_list("position", flat=True))
    dup = len(pos) != len(set(pos))
    assert (not dup) if locked else dup, pos
    tl2 = svc.create_list(a, "L2")
    _race(
        monkeypatch,
        locked,
        2,
        [lambda: svc.delete_list(a, tl2), lambda: svc.create_task(a, tl2, "late")],
    )
    orphan = Task.objects.filter(task_list=tl2, deleted_at__isnull=True).exists()
    assert (not orphan) if locked else orphan
