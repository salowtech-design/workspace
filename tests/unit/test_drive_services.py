# ruff: noqa: B023  (each lambda runs inside its own loop round; threads are joined before the next)
"""Drive metadata, permissions and services (no file bytes: storage is not configured)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import CustomUser
from apps.core.foundation.permissions import can
from apps.core.models import AuditLog
from apps.drive import services as svc
from apps.drive.models import DriveFile, FileVersion, Folder, Share


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid,
        full_name=eid,
        email=f"{eid.lower()}@salowtech.com",
        role="employee",
        status="active",
        is_active=True,
    )


@pytest.fixture
def people(db):
    return _u("D-A"), _u("D-B"), _u("D-C")


def _file(owner, folder=None, name="f.txt"):
    return DriveFile.objects.create(owner=owner, folder=folder, name=name)


def test_default_deny_then_inherited_roles_and_strongest_wins(people):
    a, b, _ = people
    top = svc.create_folder(a, "Projects")
    sub = svc.create_folder(a, "Q4", parent=top)
    f = _file(a, sub)
    f.refresh_from_db()  # bulk update does not touch the stale in-memory copy
    assert not can(b, "view", "drive.file", f)
    svc.share(a, top, b, "viewer")
    assert can(b, "view", "drive.file", f) and not can(b, "rename", "drive.file", f)
    svc.share(a, f, b, "editor")  # file-level grant is stronger than the folder's viewer
    assert can(b, "rename", "drive.file", f) and not can(b, "share", "drive.file", f)
    assert not can(b, "delete_forever", "drive.file", f)


def test_unshare_removes_access_immediately(people):
    a, b, _ = people
    top = svc.create_folder(a, "Docs")
    svc.share(a, top, b, "editor")
    assert can(b, "edit", "drive.folder", top)
    assert svc.unshare(a, top, b) == 1
    assert not can(b, "view", "drive.folder", top)
    with pytest.raises(PermissionDenied):
        svc.list_folder(b, top)


def test_only_owner_shares_and_inactive_people_cannot_be_granted(people):
    a, b, c = people
    top = svc.create_folder(a, "Docs")
    svc.share(a, top, b, "editor")
    with pytest.raises(PermissionDenied):
        svc.share(b, top, c, "viewer")
    c.status = "suspended"
    c.save()
    with pytest.raises(ValidationError):
        svc.share(a, top, c, "viewer")
    with pytest.raises(ValidationError):
        svc.share(a, top, b, "commenter")  # not offered until commenting exists


def test_move_checks_cycle_permission_and_names(people):
    a, b, _ = people
    p = svc.create_folder(a, "P")
    q = svc.create_folder(a, "Q", parent=p)
    with pytest.raises(ValidationError):
        svc.move(a, p, q)
    with pytest.raises(ValidationError):
        svc.move(a, p, p)
    f = _file(a, p, "x.txt")
    svc.move(a, f, q)
    f.refresh_from_db()
    assert f.folder_id == q.pk
    _file(a, p, "dup.txt")
    g = _file(a, q, "dup.txt")
    with pytest.raises(ValidationError):
        svc.move(a, g, p)
    with pytest.raises(PermissionDenied):
        svc.move(b, f, None)
    svc.share(a, p, b, "viewer")
    with pytest.raises(PermissionDenied):
        svc.move(b, f, p)  # viewer cannot move


def test_editor_cannot_take_an_owners_file_out_and_new_items_stay_with_the_folder_owner(people):
    a, b, _ = people
    top = svc.create_folder(a, "Team")
    f = _file(a, top, "plan.txt")
    svc.share(a, top, b, "editor")
    mine = svc.create_folder(b, "Mine")
    with pytest.raises(ValidationError):
        svc.move(b, f, mine)  # into the editor's own drive: refused, owner keeps the file
    with pytest.raises(PermissionDenied):
        svc.move(b, f, None)  # top level is owner only
    made = svc.create_folder(b, "By B", parent=top)
    assert made.owner_id == a.pk and made.created_by_id == b.pk
    other = svc.create_folder(b, "B top")
    svc.share(a, f, b, "editor")
    svc.share(b, other, a, "editor")
    with pytest.raises(ValidationError):
        svc.move(b, f, other)  # cross-owner moves refused in v1


def test_inherited_access_cannot_be_unshared_on_the_child_and_commenter_is_not_offered(people):
    a, b, _ = people
    top = svc.create_folder(a, "P")
    sub = svc.create_folder(a, "S", parent=top)
    svc.share(a, top, b, "viewer")
    with pytest.raises(ValidationError):
        svc.unshare(a, sub, b)
    assert can(b, "view", "drive.folder", sub)
    with pytest.raises(ValidationError):
        svc.share(a, _file(a, top), b, "commenter")


def test_names_depth_and_journal(people):
    a, _, _ = people
    for bad in ("", "a/b", "a\\b", "..", "x\ny", "z" * 201):
        with pytest.raises(ValidationError):
            svc.create_folder(a, bad)
    node = None
    for i in range(20):
        node = svc.create_folder(a, f"d{i}", parent=node)
    with pytest.raises(ValidationError):
        svc.create_folder(a, "too deep", parent=node)
    from apps.drive.models import DriveChange

    f = svc.create_folder(a, "J")
    svc.trash(a, f)
    acts = list(
        DriveChange.objects.filter(item_type="folder", item_id=f.pk).values_list(
            "action", flat=True
        )
    )
    assert acts == ["create", "trash"]
    assert Folder.objects.filter(pk=f.pk).exists()  # tombstone: the row stays


def test_trash_and_restore_move_a_folder_and_contents_as_one_unit(people):
    a, b, _ = people
    top = svc.create_folder(a, "T")
    sub = svc.create_folder(a, "S", parent=top)
    f1, f2 = _file(a, top, "1"), _file(a, sub, "2")
    svc.share(a, top, b, "editor")
    svc.trash(b, sub)  # an editor may trash; nothing is deleted
    for obj in (sub, f2):
        obj.refresh_from_db()
        assert obj.trashed_at is not None and obj.trashed_by_id == b.pk
    f1.refresh_from_db()
    assert f1.trashed_at is None
    assert not can(a, "rename", "drive.folder", sub)  # owner can only view or restore or delete
    assert can(a, "restore", "drive.folder", sub)
    assert svc.list_folder(a, top)[0] == []
    svc.restore(a, sub)
    f2.refresh_from_db()
    assert f2.trashed_at is None and svc.list_folder(a, top)[0] == [sub]
    assert Folder.objects.count() == 2 and DriveFile.objects.count() == 2


def test_nothing_is_purged_automatically(people):
    a, _, _ = people
    f = _file(a, None, "old")
    DriveFile.objects.filter(pk=f.pk).update(trashed_at="2020-01-01T00:00:00Z")
    assert DriveFile.objects.filter(pk=f.pk).exists()
    assert not hasattr(svc, "purge_trash") and not hasattr(svc, "prune_versions")


def test_shared_with_me_lists_direct_shares_only_and_hides_trash(people):
    a, b, _ = people
    top = svc.create_folder(a, "Shared")
    f = _file(a, top, "in.txt")
    svc.share(a, top, b, "viewer")
    assert [i.pk for i, _ in svc.shared_with_me(b)] == [top.pk]
    svc.trash(a, top)
    assert svc.shared_with_me(b) == []
    f.refresh_from_db()  # bulk update does not touch the stale in-memory copy
    assert not can(b, "view", "drive.file", f)


def test_audit_rows_carry_the_acting_user(people):
    a, b, _ = people
    top = svc.create_folder(a, "Aud")
    svc.share(a, top, b, "viewer")
    svc.rename(a, top, "Aud2")
    svc.unshare(a, top, b)
    acts = dict(
        AuditLog.objects.filter(action__startswith="drive.").values_list("action", "actor_id")
    )
    assert set(acts) >= {"drive.folder.create", "drive.share", "drive.rename", "drive.unshare"}
    assert set(acts.values()) == {a.pk}
    assert Share.objects.count() == 0


def test_restore_checks_name_collision_and_blocks_editor_rehoming(people):
    a, b, _ = people
    top = svc.create_folder(a, "R")
    f = _file(a, top, "same.txt")
    svc.share(a, top, b, "editor")
    svc.trash(b, f)  # b trashes one file
    _file(a, top, "same.txt")  # a different live file takes the name
    f.refresh_from_db()
    with pytest.raises(ValidationError):
        svc.restore(b, f)  # collision: nothing changes
    f.refresh_from_db()
    assert f.trashed_at is not None and f.trash_batch is not None
    # folder trashed with its file as one batch; the file's own earlier trash stays separate
    sub = svc.create_folder(a, "Sub", parent=top)
    g = _file(a, sub, "g.txt")
    svc.trash(a, g)
    svc.trash(b, sub)
    sub.refresh_from_db()
    g.refresh_from_db()
    assert g.trash_batch != sub.trash_batch
    svc.trash(a, top)
    top.refresh_from_db()
    sub.refresh_from_db()
    with pytest.raises(ValidationError):
        svc.restore(b, sub)  # parent still in the trash: an editor may not re-home it
    # owner may bring a nested batch back at the top level
    svc.restore(a, sub)
    sub.refresh_from_db()
    assert sub.parent_id is None and sub.trashed_at is None
    g.refresh_from_db()
    assert g.trashed_at is not None  # separate earlier batch is untouched


def test_deleting_an_owner_with_items_is_protected(people):
    from django.db.models import ProtectedError

    a, _, _ = people
    svc.create_folder(a, "Keep")
    with pytest.raises(ProtectedError):
        a.delete()


def test_share_target_must_be_valid_and_live(people):
    a, b, _ = people
    top = svc.create_folder(a, "S")
    svc.trash(a, top)
    top.refresh_from_db()
    with pytest.raises(PermissionDenied):
        svc.share(a, top, b, "viewer")
    svc.restore(a, top)
    top.refresh_from_db()
    svc.share(a, top, b, "viewer")
    from apps.drive.models import DriveChange

    ch = DriveChange.objects.get(action="share")
    assert ch.subject_id == b.pk


# ---- real concurrent tests (row locks only exist on TiDB/MySQL) ----


def _threads(fns):
    import threading

    from django.db import connection

    barrier = threading.Barrier(len(fns))
    out = [None] * len(fns)

    def run(i, fn):
        try:
            barrier.wait(timeout=10)
            fn()
            out[i] = "ok"
        except (ValidationError, PermissionDenied) as exc:
            out[i] = f"refused:{type(exc).__name__}"
        except Exception as exc:  # noqa: BLE001
            out[i] = f"error:{type(exc).__name__}:{exc}"
        finally:
            connection.close()

    ts = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(fns)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    return out


def _need_mysql():
    from django.db import connection

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")


@pytest.mark.django_db(transaction=True)
def test_concurrent_opposite_moves_never_make_a_cycle():
    _need_mysql()
    a = _u("D-RA")
    fa, fb = svc.create_folder(a, "A"), svc.create_folder(a, "B")
    res = _threads(
        [
            lambda: svc.move(a, Folder.objects.get(pk=fa.pk), Folder.objects.get(pk=fb.pk)),
            lambda: svc.move(a, Folder.objects.get(pk=fb.pk), Folder.objects.get(pk=fa.pk)),
        ]
    )
    assert res.count("ok") == 1, res
    fa.refresh_from_db()
    fb.refresh_from_db()
    assert (fa.parent_id is None) or (fb.parent_id is None)


@pytest.mark.django_db(transaction=True)
def test_concurrent_same_name_creates_allow_exactly_one():
    _need_mysql()
    a = _u("D-RB")
    res = _threads([lambda: svc.create_folder(a, "Same")] * 4)
    assert res.count("ok") == 1, res
    assert Folder.objects.filter(owner=a, name="Same").count() == 1


@pytest.mark.django_db(transaction=True)
def test_create_racing_a_trash_never_leaves_a_live_child_in_a_trashed_folder():
    _need_mysql()
    a = _u("D-RC")
    for i in range(5):
        p = svc.create_folder(a, f"P{i}")
        _threads(
            [
                lambda p=p, i=i: svc.create_folder(a, f"c{i}", parent=Folder.objects.get(pk=p.pk)),
                lambda p=p: svc.trash(a, Folder.objects.get(pk=p.pk)),
            ]
        )
        p.refresh_from_db()
        assert p.trashed_at is not None
        live = Folder.objects.filter(parent=p, trashed_at__isnull=True)
        assert not live.exists(), "a live child sits inside a trashed folder"


@pytest.mark.django_db(transaction=True)
def test_control_without_the_tree_lock_the_same_name_race_is_visible(monkeypatch):
    """Sensitivity control: shows the race test above can fail when the lock is missing."""
    _need_mysql()
    monkeypatch.setattr(svc, "_lock_tree", lambda owner_id: None)
    a = _u("D-RD")
    res = _threads([lambda: svc.create_folder(a, "Same")] * 6)
    assert res.count("ok") > 1, res


@pytest.mark.django_db(transaction=True)
def test_isolation_level_is_read_committed_so_reads_after_the_lock_are_current():
    _need_mysql()
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT @@transaction_isolation")
        level = cur.fetchone()[0]
    assert level.upper().replace("-", " ").replace("_", " ") == "READ COMMITTED", level


@pytest.mark.django_db(transaction=True)
def test_opposite_moves_never_cycle_over_many_rounds():
    _need_mysql()
    a = _u("D-RE")
    ok_rounds = 0
    for i in range(20):
        fa, fb = svc.create_folder(a, f"A{i}"), svc.create_folder(a, f"B{i}")
        res = _threads(
            [
                lambda: svc.move(a, Folder.objects.get(pk=fa.pk), Folder.objects.get(pk=fb.pk)),
                lambda: svc.move(a, Folder.objects.get(pk=fb.pk), Folder.objects.get(pk=fa.pk)),
            ]
        )
        fa.refresh_from_db()
        fb.refresh_from_db()
        assert res.count("ok") == 1, res
        assert fa.parent_id is None or fb.parent_id is None
        ok_rounds += 1
    assert ok_rounds == 20


@pytest.mark.django_db(transaction=True)
def test_control_without_the_tree_lock_opposite_moves_can_cycle(monkeypatch):
    """Sensitivity control for the cycle race: with no lock, a cycle shows up in some round."""
    _need_mysql()
    monkeypatch.setattr(svc, "_lock_users", lambda *ids: None)
    a = _u("D-RF")
    cycles = 0
    for i in range(30):
        fa, fb = svc.create_folder(a, f"A{i}"), svc.create_folder(a, f"B{i}")
        _threads(
            [
                lambda: svc.move(a, Folder.objects.get(pk=fa.pk), Folder.objects.get(pk=fb.pk)),
                lambda: svc.move(a, Folder.objects.get(pk=fb.pk), Folder.objects.get(pk=fa.pk)),
            ]
        )
        fa.refresh_from_db()
        fb.refresh_from_db()
        if fa.parent_id is not None and fb.parent_id is not None:
            cycles += 1
    assert cycles >= 1, "no cycle appeared in 30 unlocked rounds; the control cannot show the race"


@pytest.mark.django_db(transaction=True)
def test_mutual_shares_do_not_deadlock():
    _need_mysql()
    a, b = _u("D-RG"), _u("D-RH")
    for i in range(10):
        fa, fb = svc.create_folder(a, f"FA{i}"), svc.create_folder(b, f"FB{i}")
        res = _threads(
            [
                lambda: svc.share(a, Folder.objects.get(pk=fa.pk), b, "viewer"),
                lambda: svc.share(b, Folder.objects.get(pk=fb.pk), a, "viewer"),
            ]
        )
        assert res == ["ok", "ok"], res


def _hex(n):
    return f"{n:064x}"


@pytest.mark.django_db
def test_version_compare_and_set_sequential():
    a, b = _u("VV-A"), _u("VV-B")
    top = svc.create_folder(a, "Docs")
    f = svc.create_file(a, "plan.txt", top)
    assert f.current_version == 0
    v1 = svc.add_version(a, f, 0, _hex(1), 10, _hex(101))
    assert v1.number == 1
    with pytest.raises(svc.VersionConflict):
        svc.add_version(a, f, 0, _hex(2), 11, _hex(102))  # stale base
    with pytest.raises(PermissionDenied):
        svc.add_version(b, f, 1, _hex(3), 5, _hex(103))  # no access
    svc.share(a, top, b, "viewer")
    with pytest.raises(PermissionDenied):
        svc.add_version(b, f, 1, _hex(3), 5, _hex(103))  # viewer cannot save
    svc.share(a, top, b, "editor")
    v2 = svc.add_version(b, f, 1, _hex(4), 20, _hex(104))
    assert v2.number == 2 and v2.uploaded_by_id == b.pk
    f.refresh_from_db()
    assert f.current_version == 2 and f.size == 20 and f.owner_id == a.pk
    for bad in (("zz", _hex(1)), (_hex(5), "short")):
        with pytest.raises(ValidationError):
            svc.add_version(a, f, 2, bad[0], 1, bad[1])
    svc.trash(a, f)
    with pytest.raises((ValidationError, PermissionDenied)):
        svc.add_version(a, f, 2, _hex(6), 1, _hex(106))
    assert list(f.versions.values_list("number", flat=True).order_by("number")) == [1, 2]


@pytest.mark.django_db(transaction=True)
def test_concurrent_saves_from_same_base_exactly_one_wins_and_numbers_stay_unique():
    _need_mysql()
    a = _u("VV-C")
    f = svc.create_file(a, "race.txt")
    fns = [
        (
            lambda i=i: svc.add_version(
                a, DriveFile.objects.get(pk=f.pk), 0, _hex(10 + i), 1, _hex(200 + i)
            )
        )
        for i in range(6)
    ]
    out = _threads(fns)
    assert out.count("ok") == 1 and out.count("refused:VersionConflict") == 5, out
    assert DriveFile.objects.get(pk=f.pk).current_version == 1
    assert FileVersion.objects.filter(file=f).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_saves_chained_from_current_never_duplicate_a_number():
    _need_mysql()
    a = _u("VV-D")
    f = svc.create_file(a, "chain.txt")

    def save(i):
        def run():
            for _ in range(10):  # retry on conflict, like a client that re-reads
                cur = DriveFile.objects.get(pk=f.pk).current_version
                try:
                    return svc.add_version(
                        a, DriveFile.objects.get(pk=f.pk), cur, _hex(30 + i), 1, _hex(300 + i)
                    )
                except svc.VersionConflict:
                    continue

        return run

    res = _threads([save(i) for i in range(5)])
    assert res == ["ok"] * 5, res
    nums = sorted(FileVersion.objects.filter(file=f).values_list("number", flat=True))
    assert nums == list(range(1, len(nums) + 1)) and len(nums) == 5
    assert DriveFile.objects.get(pk=f.pk).current_version == 5


@pytest.mark.django_db(transaction=True)
def test_control_without_the_lock_same_base_saves_are_not_exclusive(monkeypatch):
    """Sensitivity control: with the owner lock removed the race shows up, so the passing test
    above is able to fail."""
    _need_mysql()
    a = _u("VV-E")
    seen = False
    for rnd in range(10):
        f = svc.create_file(a, f"ctl{rnd}.txt")
        monkeypatch.setattr(svc, "_lock_tree", lambda owner_id: None)
        out = _threads(
            [
                (
                    lambda i=i: svc.add_version(
                        a, DriveFile.objects.get(pk=f.pk), 0, _hex(60 + i), 1, _hex(600 + i)
                    )
                )
                for i in range(6)
            ]
        )
        monkeypatch.undo()
        if out.count("ok") != 1:
            seen = True
            break
    assert seen, "no anomaly in 10 unlocked rounds; the control proves nothing"


@pytest.mark.django_db
def test_share_survives_a_notification_failure(monkeypatch):
    a, b = _u("VN-A"), _u("VN-B")
    top = svc.create_folder(a, "Keep")

    def boom(*args, **kwargs):
        raise RuntimeError("notify down")

    monkeypatch.setattr(svc, "notify", boom)
    svc.share(a, top, b, "viewer")
    assert Share.objects.filter(folder=top, user=b).exists()
    assert AuditLog.objects.filter(action="drive.share").exists()
