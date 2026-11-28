"""Drive: restore and blind-destination move over HTTP, with full-row before/after snapshots (same exclusions as
test_drive_share_http.py). One file owned by A, shared to V as viewer and E as editor; one private folder of A's that neither V
nor E can see.
What these tests do show:
  - V's move POST into A's private folder changes no row, and E's does not either. The destination is invisible to both, so
    this is a blind-destination refusal and does NOT isolate the viewer role: whether a viewer may move into a folder the viewer
    CAN see is not tested. A's same move payload moves the file (control);
  - V's restore POST on a file A trashed changes no row; A's own restore takes effect (control). This shows V was refused on this
    one file, not a general role rule;
  - E trashes the file (trashed_by is E) and E restores it: the person who trashed it can restore it.
What they do not show: the commenter role (ds.share cannot create a commenter share today, so no such share was set up here; this
is about the service refusing it now, not a claim that a commenter can never exist), another person's trash restored by an editor,
the inherited-editor path, a visible destination, a move that widens sharing (the confirm page), and a direct download (file
bytes are device-local by the storage rule, so no server download route was hit). One machine, one run, test client without CSRF;
"changes" means any row field. Not an authorization proof."""

import pytest
from django.apps import apps as django_apps
from django.test import Client

from apps.accounts.models import CustomUser
from apps.drive import services as ds
from apps.drive.models import DriveFile

HOST = "app.salowtech.com"
EXCLUDED = {"accounts.authevent", "accounts.loginattempt", "core.ratelimitbucket", "core.ratelimitrecord", "sessions.session",
            "certificates.certificateverificationlog", "face.faceverificationlog"}  # fmt: skip


def _u(eid):
    return CustomUser.objects.create(employee_id=eid, full_name=f"{eid} Person", email=f"{eid.lower()}@salowtech.com",
                                     role="employee", status="active", is_active=True)  # fmt: skip


def _snapshot():
    snap = {}
    for m in django_apps.get_models():
        label = m._meta.label_lower
        if m._meta.proxy or label in EXCLUDED:
            continue
        for row in m.objects.all().values():
            row.pop("last_login", None)
            row.pop("last_activity", None)
            snap[(label, str(row.get("id", row.get("pk", row))))] = repr(sorted(row.items(), key=lambda kv: kv[0]))
    return snap


def _changed(b, a):
    return sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))


@pytest.fixture
def world(db):
    a, v, e = _u("DM-A"), _u("DM-V"), _u("DM-E")
    f = ds.create_file(a, "ZqRoles file.txt")
    ds.share(a, f, v, "viewer")
    ds.share(a, f, e, "editor")
    priv = ds.create_folder(a, "ZqPrivate")
    return {"a": a, "v": v, "e": e, "f": f, "priv": priv}


def _post(user, n, url, data):
    cl = Client(HTTP_HOST=HOST, REMOTE_ADDR=f"10.6.0.{n}")
    cl.force_login(user)
    r = cl.post(url, data)
    assert r.status_code < 500, (url, r.status_code)
    return r


def _p(f, priv):
    b = f"/drive/file/{f.public_id}/"
    return {"rename": (b + "rename/", {"name": "ZqChanged.txt"}), "trash": (b + "trash/", {}), "restore": (b + "restore/", {}),
            "share": (b + "share/", {"employee_id": "DM-A", "role": "viewer"}), "unshare": (b + "unshare/", {"employee_id": "DM-V"}),
            "version": (b + "version/", {"base_version": 0, "size": 5, "sha256": "a" * 64}),
            "move": (b + "move/", {"destination": str(priv.public_id)})}  # fmt: skip


def test_viewer_restore_refused_and_blind_destination_move_refused_owner_controls_work(world):
    w = world
    f = w["f"]
    paths = _p(f, w["priv"])
    _post(w["a"], 1, *paths["trash"])
    f.refresh_from_db()
    assert f.trashed_at is not None, "control: the owner's trash took effect"
    before = _snapshot()
    r = _post(w["v"], 2, *paths["restore"])
    assert not _changed(before, _snapshot()), f"viewer restored (status {r.status_code})"
    _post(w["a"], 3, *paths["restore"])
    f.refresh_from_db()
    assert f.trashed_at is None, "control: the owner's restore did not take effect"
    before = _snapshot()
    r = _post(w["v"], 4, *paths["move"])  # destination invisible to V: a blind-destination refusal
    assert not _changed(before, _snapshot()), f"viewer blind move changed rows (status {r.status_code})"
    _post(w["a"], 5, *paths["move"])
    f.refresh_from_db()
    assert f.folder_id == w["priv"].pk, "control: the owner's move did not take effect"


def test_editor_restores_own_trash_and_blind_destination_move_refused(world):
    w = world
    f = w["f"]
    paths = _p(f, w["priv"])
    before = _snapshot()
    r = _post(w["e"], 1, *paths["move"])  # the private folder is not visible to the editor
    assert not _changed(before, _snapshot()), f"editor moved into a folder they cannot see (status {r.status_code})"
    _post(w["e"], 2, *paths["trash"])
    f.refresh_from_db()
    assert f.trashed_at is not None and f.trashed_by_id == w["e"].pk
    _post(w["e"], 3, *paths["restore"])
    f.refresh_from_db()
    assert f.trashed_at is None, "the editor who trashed the file could not restore it"
    _post(w["a"], 4, *paths["move"])
    assert DriveFile.objects.get(pk=f.pk).folder_id == w["priv"].pk, "control: the owner's same move payload works"
