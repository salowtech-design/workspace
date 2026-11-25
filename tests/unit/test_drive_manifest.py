"""effective_entry: trashed visibility is never active sync eligibility."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import CustomUser
from apps.drive import manifest as m
from apps.drive import services as svc


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid,
        full_name=eid,
        email=f"{eid.lower()}@salowtech.com",
        role="employee",
        status="active",
        is_active=True,
    )


@pytest.mark.django_db
def test_states_for_owner_sharee_stranger_and_trash():
    a, b, c = _u("M-A"), _u("M-B"), _u("M-C")
    top = svc.create_folder(a, "Top")
    sub = svc.create_folder(a, "Sub", top)
    f = svc.create_file(a, "x.txt", sub)
    svc.share(a, top, b, "editor")
    assert m.effective_entry(a, f) == m.Entry(m.ACTIVE, "owner")
    assert m.effective_entry(b, f) == m.Entry(m.ACTIVE, "editor")
    assert m.effective_entry(c, f) == m.Entry(m.NONE, None)
    # b (editor) trashes the sub folder: owner and trasher see it as trash only
    svc.trash(b, sub)
    sub.refresh_from_db()
    f.refresh_from_db()
    for who in (a, b):
        assert m.effective_entry(who, sub).state == m.TRASHED
        assert m.effective_entry(who, f).state in (m.TRASHED, m.NONE)
    assert m.effective_entry(c, sub) == m.Entry(m.NONE, None)
    svc.restore(a, sub)
    sub.refresh_from_db()
    f.refresh_from_db()
    assert m.effective_entry(b, f).state == m.ACTIVE


@pytest.mark.django_db
def test_trashed_never_reported_active_even_when_only_an_ancestor_is_trashed():
    a, b = _u("M-D"), _u("M-G")
    top = svc.create_folder(a, "T")
    sub = svc.create_folder(a, "S", top)
    f = svc.create_file(a, "y.txt", sub)
    svc.share(a, top, b, "viewer")
    # simulate an item whose own flag is clear while an ancestor is in the trash
    type(top).objects.filter(pk=top.pk).update(trashed_at=top.created_at)
    top.refresh_from_db()
    sub.refresh_from_db()
    f.refresh_from_db()
    assert m.effective_entry(a, f).state == m.TRASHED
    assert m.effective_entry(a, sub).state == m.TRASHED
    assert m.effective_entry(b, f) == m.Entry(m.NONE, None)  # sharee loses it, owner sees trash


@pytest.mark.django_db
def test_inactive_person_and_after_unshare_and_after_move_out():
    a, b = _u("M-E"), _u("M-F")
    top = svc.create_folder(a, "Shared")
    other = svc.create_folder(a, "Private")
    inner = svc.create_folder(a, "Inner", top)
    svc.share(a, top, b, "viewer")
    assert m.effective_entry(b, inner).state == m.ACTIVE
    svc.move(a, inner, other)
    inner.refresh_from_db()
    assert m.effective_entry(b, inner) == m.Entry(m.NONE, None)  # inherited access lost by a move
    svc.unshare(a, top, b)
    top.refresh_from_db()
    assert m.effective_entry(b, top) == m.Entry(m.NONE, None)
    svc.share(a, top, b, "viewer")
    CustomUser.objects.filter(pk=b.pk).update(is_active=False, status="inactive")
    b.refresh_from_db()
    assert m.effective_entry(b, top) == m.Entry(m.NONE, None)


# ---- manifest rows ----


def _rows(user):
    from apps.drive.models import ManifestChange

    return [
        (r.epoch, r.item_type, r.scope, r.state, r.role, r.reason)
        for r in ManifestChange.objects.filter(user=user).order_by("epoch")
    ]


@pytest.mark.django_db
def test_exact_rows_for_share_trash_restore_unshare_and_epochs_are_gap_free():
    a, b = _u("R-A"), _u("R-B")
    top = svc.create_folder(a, "Top")
    svc.share(a, top, b, "editor")
    svc.trash(a, top)
    top.refresh_from_db()
    svc.restore(a, top)
    svc.unshare(a, top, b)
    assert _rows(b) == [
        (1, "folder", "subtree", "active", "editor", "share"),
        (2, "folder", "subtree", "revoked", "", "trash"),  # sharee loses it while in trash
        (3, "folder", "subtree", "active", "editor", "restore"),  # recomputed, live access
        (4, "folder", "subtree", "revoked", "", "unshare"),
    ]
    assert [r[0] for r in _rows(a)] == [1, 2]  # owner: trash, restore
    owner_states = [r[3] for r in _rows(a)]
    assert owner_states == ["trashed", "active"]


@pytest.mark.django_db
def test_restore_uses_live_access_not_the_old_shares():
    a, b = _u("R-C"), _u("R-D")
    top = svc.create_folder(a, "Top2")
    svc.share(a, top, b, "viewer")
    svc.trash(a, top)
    top.refresh_from_db()
    from apps.drive.models import Share

    Share.objects.filter(folder=top, user=b).delete()  # share gone while the folder was trashed
    top.refresh_from_db()
    svc.restore(a, top)
    # b gets no new grant: the last row b has is still the revoke written at trash time
    assert [r[3] for r in _rows(b)][-1] == "revoked"
    assert all(r[5] != "restore" or r[3] == "revoked" for r in _rows(b))


@pytest.mark.django_db
def test_unshare_folder_keeps_an_independent_file_grant_active():
    a, b = _u("R-F"), _u("R-G")
    top = svc.create_folder(a, "Proj")
    f = svc.create_file(a, "keep.txt", top)
    svc.share(a, top, b, "viewer")
    svc.share(a, f, b, "viewer")
    svc.unshare(a, top, b)
    f.refresh_from_db()
    assert m.effective_entry(b, f).state == m.ACTIVE
    rows = _rows(b)
    assert rows[-2][1:4] == ("folder", "subtree", "revoked")
    assert rows[-1][1:4] == ("file", "item", "active") and rows[-1][5] == "unshare_keep"


@pytest.mark.django_db
def test_trash_writes_revoke_for_nested_share_roots_not_one_row_per_file():
    from apps.drive.models import ManifestChange

    a, b = _u("R-H"), _u("R-I")
    top = svc.create_folder(a, "Wide")
    for i in range(40):
        svc.create_file(a, f"f{i}.txt", top)
    svc.share(a, svc.create_file(a, "shared.txt", top), b, "viewer")
    before = ManifestChange.objects.count()
    top.refresh_from_db()
    svc.trash(a, top)
    # owner row for the folder plus one row for b's nested file share: not 41 rows
    assert ManifestChange.objects.count() - before == 2


@pytest.mark.django_db
def test_cross_owner_move_is_refused_and_writes_no_manifest_rows():
    from django.core.exceptions import PermissionDenied, ValidationError

    from apps.drive.models import ManifestChange

    a, b = _u("R-J"), _u("R-K")
    mine = svc.create_folder(a, "Mine")
    theirs = svc.create_folder(b, "Theirs")
    svc.share(b, theirs, a, "editor")
    before = ManifestChange.objects.count()
    with pytest.raises((ValidationError, PermissionDenied)):
        svc.move(a, mine, theirs)
    assert ManifestChange.objects.count() == before


@pytest.mark.django_db
def test_reconcile_flags_without_locks_and_corrects_under_locks():
    from apps.drive.models import Share

    a, b = _u("R-L"), _u("R-M")
    top = svc.create_folder(a, "Rec")
    svc.share(a, top, b, "viewer")
    assert svc.reconcile_user(b) == []
    Share.objects.filter(folder=top, user=b).delete()  # state changed behind the manifest's back
    flagged = svc.reconcile_user(b)
    assert len(flagged) == 1
    assert len(_rows(b)) == 1  # flagging wrote nothing
    assert len(svc.reconcile_user(b, fix=True)) == 1
    assert _rows(b)[-1][3:6] == ("revoked", "", "reconcile")
    assert svc.reconcile_user(b) == []


@pytest.mark.django_db
def test_manifest_since_reads_epoch_first_and_flags_new_people():
    a, b = _u("R-N"), _u("R-O")
    first = svc.manifest_since(b)
    assert first["full_list_required"] is True and first["changes"] == [] and first["epoch"] == 0
    top = svc.create_folder(a, "Feed")
    svc.share(a, top, b, "viewer")
    out = svc.manifest_since(b, since=0)
    assert out["epoch"] == 1 and out["full_list_required"] is False and len(out["changes"]) == 1
    assert out["next_since"] == 1 and out["has_more"] is False
    assert svc.manifest_since(b, since=1)["changes"] == []
    assert svc.manifest_since(b, since=99)["full_list_required"] is True  # cursor ahead of server


# ---- concurrency (TiDB/MySQL only) ----


def _need_mysql():
    from django.db import connection

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")


def _epochs(user):
    return [r[0] for r in _rows(user)]


@pytest.mark.django_db(transaction=True)
def test_two_owners_sharing_to_one_recipient_get_distinct_gap_free_epochs():
    from tests.unit.test_drive_services import _threads

    _need_mysql()
    r = _u("C-R")
    owners = [_u(f"C-O{i}") for i in range(4)]
    folders = [svc.create_folder(o, "F") for o in owners]
    out = _threads(
        [
            (lambda o=o, f=f: svc.share(o, f, r, "viewer"))
            for o, f in zip(owners, folders, strict=True)
        ]
    )
    assert out == ["ok"] * 4, out
    assert _epochs(r) == [1, 2, 3, 4]


def _bump_in_threads(r, folders, take_lock):
    """Call the epoch writer directly from several threads, with or without the account lock.
    Going through share() would not show it: audit logging already serialises those calls."""
    import time

    from django.db import transaction

    from apps.drive.models import ManifestEpoch
    from tests.unit.test_drive_services import _threads

    ManifestEpoch.objects.get_or_create(user=r)
    real_save = ManifestEpoch.save

    def slow_save(self, *a, **k):  # widen the read-modify-write window
        time.sleep(0.05)
        return real_save(self, *a, **k)

    def one(folder):
        def run():
            with transaction.atomic():
                if take_lock:
                    svc._lock_users(r.pk)
                svc._bump(r.pk, folder, "probe")

        return run

    ManifestEpoch.save = slow_save
    try:
        return _threads([one(f) for f in folders])
    finally:
        ManifestEpoch.save = real_save


@pytest.mark.django_db(transaction=True)
def test_epoch_writer_with_the_account_lock_is_gap_free_and_without_it_collides():
    _need_mysql()
    a = _u("C-X")
    folders = [svc.create_folder(a, f"P{i}") for i in range(4)]
    locked = _u("C-L")
    assert _bump_in_threads(locked, folders, True) == ["ok"] * 4
    assert _epochs(locked) == [1, 2, 3, 4]
    dup = False
    for i in range(5):
        u = _u(f"C-V{i}")
        _bump_in_threads(u, folders, False)
        ep = _epochs(u)
        if len(set(ep)) < len(ep):  # the specific anomaly: two writers took the same epoch
            dup = True
            break
    assert dup, "no duplicate epoch without the lock in 5 rounds; the control proves nothing"


@pytest.mark.django_db(transaction=True)
def test_lock_set_growth_between_read_and_lock_is_retried_from_scratch(monkeypatch):
    import threading

    from django.db import connection

    from apps.drive.models import Share

    _need_mysql()
    a, b, c = _u("G-A"), _u("G-B"), _u("G-C")
    top = svc.create_folder(a, "Grow")
    svc.share(a, top, b, "viewer")
    calls = {"n": 0}
    real = svc._lock_users

    def grow_once(*ids):
        calls["n"] += 1
        if calls["n"] == 1:  # another connection adds a person after the set was read

            def other():
                try:
                    Share.objects.create(folder=top, user=c, role="viewer", granted_by=a)
                finally:
                    connection.close()

            t = threading.Thread(target=other)
            t.start()
            t.join()
        return real(*ids)

    monkeypatch.setattr(svc, "_lock_users", grow_once)
    svc.trash(a, top)
    monkeypatch.undo()
    assert calls["n"] >= 2  # first attempt saw the set grow and was redone
    assert {r[5] for r in _rows(c)} == {"trash"}  # the late person is covered by the retry


@pytest.mark.django_db(transaction=True)
def test_stale_reconcile_cannot_overwrite_a_concurrent_unshare():
    from tests.unit.test_drive_services import _threads

    _need_mysql()
    a, b = _u("K-A"), _u("K-B")
    top = svc.create_folder(a, "Race")
    svc.share(a, top, b, "viewer")
    for _ in range(5):  # unshare and reshare while reconcile runs; truth must win
        out = _threads(
            [
                lambda: svc.unshare(a, top, b),
                lambda: svc.reconcile_user(b, fix=True),
            ]
        )
        assert all(o == "ok" for o in out), out
        svc.reconcile_user(b, fix=True)
        live = m.effective_entry(b, top).state
        last = _rows(b)[-1][3]
        assert (live, last) in ((m.NONE, "revoked"), (m.ACTIVE, "active"))
        svc.share(a, top, b, "viewer")
    assert _epochs(b) == list(range(1, len(_epochs(b)) + 1))


def test_only_lock_contention_errors_are_retried():
    from django.db import OperationalError

    ok = svc._is_lock_contention
    assert ok(OperationalError(1213, "Deadlock found"))
    assert ok(OperationalError(1205, "Lock wait timeout exceeded"))
    assert ok(OperationalError(1105, "pessimistic lock retry limit reached"))
    assert not ok(OperationalError(1105, "unknown column"))  # generic TiDB error: a real fault
    assert not ok(OperationalError(2006, "server has gone away"))
    assert not ok(OperationalError("no code"))


@pytest.mark.django_db
def test_a_permanent_operational_error_is_not_retried(monkeypatch):
    from django.db import OperationalError

    a = _u("E-A")
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise OperationalError(1105, "unknown column 'x'")

    monkeypatch.setattr(svc, "_lock_affected", boom)
    top = svc.create_folder(a, "Err")
    with pytest.raises(OperationalError):
        svc.trash(a, top)
    assert calls["n"] == 1


@pytest.mark.django_db
def test_reader_pages_by_epoch_and_refuses_inactive_accounts():
    a, b = _u("P-A"), _u("P-B")
    top = svc.create_folder(a, "Page")
    for _ in range(3):
        svc.share(a, top, b, "viewer")
        svc.unshare(a, top, b)
    total = svc.manifest_since(b, limit=1000)
    assert [c.epoch for c in total["changes"]] == [1, 2, 3, 4, 5, 6]
    p1 = svc.manifest_since(b, since=0, limit=4)
    assert (
        [c.epoch for c in p1["changes"]] == [1, 2, 3, 4]
        and p1["has_more"]
        and p1["next_since"] == 4
    )
    p2 = svc.manifest_since(b, since=p1["next_since"], limit=4)
    assert [c.epoch for c in p2["changes"]] == [5, 6] and not p2["has_more"]
    CustomUser.objects.filter(pk=b.pk).update(is_active=False, status="inactive")
    b.refresh_from_db()
    with pytest.raises(PermissionDenied):
        svc.manifest_since(b)


@pytest.mark.django_db
def test_reconcile_finds_a_grant_written_straight_into_the_database():
    from apps.drive.models import Share

    a, b = _u("D-X"), _u("D-Y")
    top = svc.create_folder(a, "Direct")
    Share.objects.create(folder=top, user=b, role="editor", granted_by=a)  # no manifest row
    assert svc.reconcile_user(b) == [("folder", top.public_id)]
    assert svc.reconcile_user(b, fix=True) == [("folder", top.public_id)]
    assert _rows(b)[-1][3:6] == ("active", "editor", "reconcile")
    assert svc.reconcile_user(b) == []
    from apps.core.models import AuditLog

    rec = AuditLog.objects.filter(action="drive.manifest.reconcile")
    assert rec.count() == 1 and rec[0].actor_id is None
    assert rec[0].payload_summary.get("person") == b.pk


@pytest.mark.django_db
def test_notification_value_errors_skip_but_database_errors_propagate(monkeypatch):
    from django.db import OperationalError

    from apps.drive.models import Share

    a, b, c = _u("N-A"), _u("N-B"), _u("N-C")
    top = svc.create_folder(a, "Notif")

    def bad_link(*args, **kwargs):
        raise RuntimeError("notify down")

    monkeypatch.setattr(svc, "notify", bad_link)
    svc.share(a, top, b, "viewer")
    assert Share.objects.filter(folder=top, user=b).exists()

    def db_down(*args, **kwargs):
        raise OperationalError(1213, "Deadlock found")

    monkeypatch.setattr(svc, "notify", db_down)
    with pytest.raises(OperationalError):
        svc.share(a, top, c, "viewer")


@pytest.mark.django_db(transaction=True)
def test_relock_retries_only_allowed_errors_end_to_end(monkeypatch):
    from django.db import OperationalError

    monkeypatch.setattr(svc.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky(errors):
        @svc._relock
        def op():
            calls["n"] += 1
            if calls["n"] <= len(errors):
                raise errors[calls["n"] - 1]
            return "ok"

        return op

    calls["n"] = 0
    assert (
        flaky([OperationalError(1213, "Deadlock found"), OperationalError(1205, "Lock wait")])()
        == "ok"
    )
    assert calls["n"] == 3
    for err in (
        OperationalError(1105, "other"),
        OperationalError(1105, "commit failed: x"),
        OperationalError(1045, "denied"),
    ):
        calls["n"] = 0
        with pytest.raises(OperationalError):
            flaky([err] * 5)()
        assert calls["n"] == 1
    calls["n"] = 0
    with pytest.raises(ValidationError):
        flaky([OperationalError(1213, "Deadlock found")] * 9)()
    assert calls["n"] == 5


@pytest.mark.django_db
def test_page_limit_is_capped(monkeypatch):
    a, b = _u("L-A"), _u("L-B")
    top = svc.create_folder(a, "Cap")
    for _ in range(3):
        svc.share(a, top, b, "viewer")
        svc.unshare(a, top, b)
    monkeypatch.setattr(svc, "MANIFEST_PAGE_EPOCHS", 2)
    out = svc.manifest_since(b, since=0, limit=10**9)
    assert [c.epoch for c in out["changes"]] == [1, 2]
    assert out["next_since"] == 2 and out["has_more"] is True


def _share_state(top, b):
    from apps.core.models import AuditLog
    from apps.drive.models import DriveChange, ManifestChange, Share

    return (
        Share.objects.filter(folder=top, user=b).count(),
        DriveChange.objects.filter(action="share").count(),
        ManifestChange.objects.filter(user=b).count(),
        AuditLog.objects.filter(action="drive.share").count(),
    )


@pytest.mark.django_db(transaction=True)
def test_commit_failure_before_real_commit_is_not_replayed(monkeypatch):
    from django.db import OperationalError, connections

    a, b = _u("K-A"), _u("K-B")
    top = svc.create_folder(a, "Commit")
    before = _share_state(top, b)
    connection = connections["default"]
    real = connection.commit
    calls = {"n": 0}

    def failing_commit():
        calls["n"] += 1
        raise OperationalError(
            1213, "Deadlock found"
        )  # no word about commit; before the real commit

    monkeypatch.setattr(connection, "commit", failing_commit)
    with pytest.raises(ValidationError):
        svc.share(a, top, b, "viewer")
    monkeypatch.setattr(connection, "commit", real)
    assert calls["n"] == 1  # one attempt, one commit: the body was not run again
    assert _share_state(top, b) == before  # rolled back: nothing written


@pytest.mark.django_db(transaction=True)
def test_commit_that_landed_then_errored_is_not_replayed(monkeypatch):
    from django.db import OperationalError, connections

    a, b = _u("M-A"), _u("M-B")
    top = svc.create_folder(a, "Landed")
    connection = connections["default"]
    real = connection.commit
    calls = {"n": 0}

    def landed_then_error():
        calls["n"] += 1
        real()
        raise OperationalError(1213, "Deadlock found")  # the commit happened, the answer was lost

    monkeypatch.setattr(connection, "commit", landed_then_error)
    with pytest.raises(ValidationError):
        svc.share(a, top, b, "viewer")
    monkeypatch.setattr(connection, "commit", real)
    assert calls["n"] == 1
    assert _share_state(top, b) == (1, 1, 1, 1)  # exactly one set of rows, no duplicates
