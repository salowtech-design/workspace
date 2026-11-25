"""Drive metadata, permissions and services (no file bytes: storage is not configured)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import CustomUser
from apps.core.foundation.permissions import can
from apps.core.models import AuditLog
from apps.drive import services as svc
from apps.drive.models import DriveFile, Folder, Share


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
