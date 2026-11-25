import datetime as dt

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from apps.accounts.models import CustomUser
from apps.events import services as es
from apps.events.models import Event, Guest

IST = "Asia/Kolkata"
D = dt.datetime


def _u(eid, status="active"):
    u = CustomUser.objects.create_user(employee_id=eid, full_name=eid)
    u.status = status
    u.save()
    return u


def _utc(y, mo, d, h=0, m=0):
    return dt.datetime(y, mo, d, h, m, tzinfo=dt.timezone.utc)


@pytest.mark.django_db
def test_create_validates_times_zone_guests_and_stores_utc():
    org, g1, off = _u("EV-ORG"), _u("EV-G1"), _u("EV-OFF", "suspended")
    ev = es.create_event(org, " Review ", D(2026, 11, 27, 10), D(2026, 11, 27, 11), IST, [g1.pk])
    assert ev.title == "Review" and ev.start_utc == _utc(2026, 11, 27, 4, 30)
    assert Guest.objects.get(event=ev).person_id == g1.pk
    ok = (D(2026, 11, 27, 10), D(2026, 11, 27, 11), IST)
    bad = [
        ("", *ok),
        ("x" * 201, *ok),
        ("t", D(2026, 11, 27, 11), D(2026, 11, 27, 10), IST),  # end before start
        ("t", D(2026, 11, 27, 10), D(2026, 11, 27, 10), IST),  # zero length
        ("t", D(2026, 11, 27, 10), D(2026, 12, 20, 10), IST),  # over 14 days
        ("t", D(2026, 11, 27, 10), D(2026, 11, 27, 11), "Mars/Base"),
        ("t", dt.datetime(2026, 11, 27, 10, tzinfo=dt.timezone.utc), D(2026, 11, 27, 11), IST),
        ("t", D(2026, 3, 8, 2, 30), D(2026, 3, 8, 3, 30), "America/New_York"),  # no such time
    ]
    for args in bad:
        with pytest.raises(ValidationError):
            es.create_event(org, *args)
    with pytest.raises(ValidationError):
        es.create_event(org, "t", *ok, guest_ids=[off.pk])
    with pytest.raises(ValidationError):
        es.create_event(org, "t", *ok, guest_ids=[999999])
    with pytest.raises(ValidationError):
        es.create_event(org, "t", *ok, guest_ids=list(range(1000, 1060)))
    ev2 = es.create_event(org, "self-invite", *ok, guest_ids=[org.pk, g1.pk, g1.pk])
    assert Guest.objects.filter(event=ev2).count() == 1  # organiser and duplicates dropped
    stale = CustomUser.objects.get(pk=org.pk)
    CustomUser.objects.filter(pk=org.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        es.create_event(stale, "t", *ok)
    assert Event.objects.count() == 2


@pytest.mark.django_db
def test_update_cancel_respond_authority_versions_and_noop():
    org, g1, out = _u("EU-ORG"), _u("EU-G1"), _u("EU-OUT")
    a = (D(2026, 11, 27, 10), D(2026, 11, 27, 11), IST)
    ev = es.create_event(org, "Plan", *a, guest_ids=[g1.pk])
    pid = ev.public_id
    for who in (g1, out):
        with pytest.raises(Http404):
            es.update_event(who, pid, 1, "Hack", *a)
        with pytest.raises(Http404):
            es.cancel_event(who, pid)
    with pytest.raises(Http404):
        es.respond(out, pid, "accepted")
    assert es.update_event(org, pid, 1, "Plan", *a).version == 1  # same values: no bump
    assert es.update_event(org, pid, 1, "Plan 2", *a).version == 2
    with pytest.raises(es.StaleEvent):
        es.update_event(org, pid, 1, "Plan 3", *a)
    assert es.respond(g1, pid, "accepted").response == "accepted"
    for bad in ("needs_action", "bogus"):
        with pytest.raises(ValidationError):
            es.respond(g1, pid, bad)
    assert es.cancel_event(org, pid).status == "cancelled"
    assert es.cancel_event(org, pid).version == 3  # repeat: no second bump
    with pytest.raises(ValidationError):
        es.update_event(org, pid, 3, "Late", *a)
    with pytest.raises(ValidationError):
        es.respond(g1, pid, "declined")
    with pytest.raises(Http404):
        es.get_event(out, pid)
    assert es.get_event(g1, pid).pk == ev.pk


@pytest.mark.django_db
def test_own_calendar_and_free_busy_show_only_busy_and_skip_declined_and_cancelled():
    org, g1, other = _u("FB-ORG"), _u("FB-G1"), _u("FB-OTH")
    day = lambda h, m=0: D(2026, 11, 27, h, m)  # noqa: E731
    e1 = es.create_event(org, "Secret board talk", day(10), day(11), IST, [g1.pk])
    es.create_event(org, "Adjacent", day(11), day(12), IST)
    e3 = es.create_event(org, "Dropped", day(14), day(15), IST, [g1.pk])
    e4 = es.create_event(org, "Cancelled", day(16), day(17), IST)
    es.respond(g1, e3.public_id, "declined")
    es.cancel_event(org, e4.public_id)
    lo, hi = _utc(2026, 11, 26), _utc(2026, 11, 29)
    assert [e.title for e in es.events_for(g1, lo, hi)] == ["Secret board talk"]
    assert [e.title for e in es.events_for(org, lo, hi)] == [
        "Secret board talk",
        "Adjacent",
        "Dropped",
    ]
    assert list(es.events_for(other, lo, hi)) == []
    fb = es.free_busy(other, [org.pk, g1.pk], lo, hi)
    assert fb[org.pk] == [
        (_utc(2026, 11, 27, 4, 30), _utc(2026, 11, 27, 6, 30)),  # merged touching
        (_utc(2026, 11, 27, 8, 30), _utc(2026, 11, 27, 9, 30)),
    ]
    assert fb[g1.pk] == [(_utc(2026, 11, 27, 4, 30), _utc(2026, 11, 27, 5, 30))]
    assert "Secret" not in repr(fb) and e1.title not in repr(fb)
    # windows and people are bounded
    with pytest.raises(ValidationError):
        es.free_busy(other, [org.pk], lo, lo + dt.timedelta(days=63))
    with pytest.raises(ValidationError):
        es.free_busy(other, [org.pk], hi, lo)
    with pytest.raises(ValidationError):
        es.free_busy(other, list(range(2000, 2060)), lo, hi)
    with pytest.raises(ValidationError):
        es.free_busy(other, [org.pk], D(2026, 11, 26), D(2026, 11, 27))  # naive
    # an event that ends exactly at the window start is not busy
    assert (
        es.free_busy(other, [org.pk], _utc(2026, 11, 27, 6, 30), _utc(2026, 11, 27, 8, 30))[org.pk]
        == []
    )


def _plain_lock(public_id):
    return Event.objects.filter(public_id=public_id).first()  # the control: no row lock


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_two_edits_with_the_same_version_only_one_wins(monkeypatch, locked):
    import threading

    from django.db import connection

    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    if not locked:
        monkeypatch.setattr(es, "_lock_event", _plain_lock)
    outcomes, lost = [], 0
    a = (D(2026, 12, 1, 10), D(2026, 12, 1, 11), IST)
    for i in range(2 if locked else 3):
        org = _u(f"RE-ORG{i}{locked}")
        ev = es.create_event(org, "Start", *a)
        barrier = threading.Barrier(2)

        def pause(barrier=barrier):
            try:
                barrier.wait(timeout=1.5 if locked else 10)  # both passed the version check
            except threading.BrokenBarrierError:
                pass  # locked: the other side is held at the row lock

        monkeypatch.setattr(es, "_pause", pause)
        outcomes.append(
            _threads(
                [
                    lambda ev=ev, org=org: es.update_event(org, ev.public_id, 1, "First", *a),
                    lambda ev=ev, org=org: es.update_event(org, ev.public_id, 1, "Second", *a),
                ]
            )
        )
        ev.refresh_from_db()
        if ev.version == 2 and sorted(outcomes[-1]) == ["ok", "ok"]:
            lost += 1  # both reported success from version 1, one silently overwrote the other
    if locked:
        assert all(sorted(o) == ["ok", "refused:StaleEvent"] for o in outcomes), outcomes
        assert lost == 0
    else:
        assert all("error" not in x for o in outcomes for x in o), outcomes
        assert lost == 3, (lost, outcomes)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_cancel_cannot_be_undone_by_an_edit_that_checked_first(monkeypatch, locked):
    import threading

    from django.db import connection

    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    if not locked:
        monkeypatch.setattr(es, "_lock_event", _plain_lock)
    a = (D(2026, 12, 2, 10), D(2026, 12, 2, 11), IST)
    revived, outcomes, saw_commit = 0, [], []
    for i in range(2):
        org = _u(f"RC-ORG{i}{locked}")
        ev = es.create_event(org, "Start", *a)
        paused, committed = threading.Event(), threading.Event()

        def pause(paused=paused, committed=committed):
            paused.set()  # the edit has passed its checks and holds what it read
            saw_commit.append(committed.wait(timeout=2.0))  # did the cancel commit meanwhile?

        def cancel(ev=ev, org=org, paused=paused, committed=committed):
            assert paused.wait(timeout=10)
            try:
                es.cancel_event(org, ev.public_id)
            finally:
                committed.set()  # set after cancel_event returned, i.e. after its commit

        monkeypatch.setattr(es, "_pause", pause)
        outcomes.append(
            _threads(
                [
                    lambda ev=ev, org=org: es.update_event(org, ev.public_id, 1, "Edited", *a),
                    cancel,
                ]
            )
        )
        ev.refresh_from_db()
        if ev.status != "cancelled":
            revived += 1
    assert all("error" not in x for o in outcomes for x in o), outcomes
    if locked:
        assert saw_commit == [False, False], saw_commit  # cancel could not commit mid-edit
        assert revived == 0, (revived, outcomes)
    else:
        assert saw_commit == [True, True], saw_commit
        assert revived == 2, (revived, outcomes)  # the stale save() wrote "confirmed" back


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_cancel_versus_response_no_answer_lands_after_the_cancel(monkeypatch, locked):
    import threading

    from django.db import connection

    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    if not locked:
        monkeypatch.setattr(es, "_lock_event", _plain_lock)
    a = (D(2026, 12, 3, 10), D(2026, 12, 3, 11), IST)
    late, outcomes, saw_commit = 0, [], []
    for i in range(2):
        org, g = _u(f"RR-ORG{i}{locked}"), _u(f"RR-G{i}{locked}")
        ev = es.create_event(org, "Start", *a, guest_ids=[g.pk])
        paused, committed = threading.Event(), threading.Event()

        def pause(paused=paused, committed=committed):
            paused.set()  # the response has passed its checks (event still confirmed)
            saw_commit.append(committed.wait(timeout=2.0))

        def cancel(ev=ev, org=org, paused=paused, committed=committed):
            assert paused.wait(timeout=10)
            try:
                es.cancel_event(org, ev.public_id)
            finally:
                committed.set()  # after cancel_event returned, i.e. after its commit

        monkeypatch.setattr(es, "_pause", pause)
        outcomes.append(
            _threads([lambda ev=ev, g=g: es.respond(g, ev.public_id, "accepted"), cancel])
        )
        ev.refresh_from_db()
        if ev.status == "cancelled" and Guest.objects.get(event=ev).response == "accepted":
            late += 1  # final state only; saw_commit is what shows the order
    assert all("error" not in x for o in outcomes for x in o), outcomes
    if locked:
        assert saw_commit == [False, False], saw_commit
        # the answer committed first, then the cancel: the answer is not "after" the cancel
        assert all(sorted(o) == ["ok", "ok"] for o in outcomes), outcomes
    else:
        assert saw_commit == [True, True], saw_commit
        assert late == 2, (late, outcomes)  # same final state as locked; the order differs


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_response_versus_edit_both_land_and_the_lock_only_orders_them(monkeypatch, locked):
    """Respond and edit touch different rows (the guest row and the event row), so there is no
    lost update to find here with or without the event lock. What the test shows: both changes
    survive, and with the lock the response cannot run while the edit is mid-write."""
    import threading

    from django.db import connection

    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    if not locked:
        monkeypatch.setattr(es, "_lock_event", _plain_lock)
    a = (D(2026, 12, 4, 10), D(2026, 12, 4, 11), IST)
    org, g = _u(f"RE2-ORG{locked}"), _u(f"RE2-G{locked}")
    ev = es.create_event(org, "Start", *a, guest_ids=[g.pk])
    edit_paused, respond_reached, release = (threading.Event() for _ in range(3))
    who = threading.local()

    def pause():
        if getattr(who, "name", "") == "edit":
            edit_paused.set()  # the edit holds its checks and (locked) the event row
            release.wait(timeout=10)
        else:
            respond_reached.set()

    monkeypatch.setattr(es, "_pause", pause)

    def edit():
        who.name = "edit"
        es.update_event(org, ev.public_id, 1, "Renamed", *a)

    def respond():
        who.name = "respond"
        assert edit_paused.wait(timeout=10)
        es.respond(g, ev.public_id, "accepted")

    seen = []

    def controller():
        assert edit_paused.wait(timeout=10)
        seen.append(respond_reached.wait(timeout=1.5))  # did the response get past its checks?
        release.set()

    out = _threads([edit, respond, controller])
    ev.refresh_from_db()
    assert out == ["ok", "ok", "ok"], out
    assert (ev.title, ev.version) == ("Renamed", 2)
    assert Guest.objects.get(event=ev).response == "accepted"
    # locked: the response waited for the edit's commit; lockless: it ran alongside the edit
    assert seen == [not locked], seen
