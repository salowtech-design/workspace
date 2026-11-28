"""Drive sharing over HTTP, with real shares and a full-row before/after snapshot (every field of every row outside the
throttle, Django-session and audit-log tables; last_login and last_activity stripped).

One file owned by A, shared to V as viewer and to E as editor, and one more file inside a folder shared to W as viewer:
  - viewer V: sees the file on the Shared page (control), and rename, trash, share, unshare and a new version change nothing;
    the SAME share, unshare and version payloads from the owner A do take effect (a Share row for C appears, V's Share row
    goes, a version 1 row appears), so the payloads are valid for those routes;
  - editor E: share and unshare (aimed at V, someone else) change nothing and V's share is still there; rename and trash
    DO set the intended fields (the file name, trashed_at);
  - viewer W by inheritance from a shared folder: the folder page lists the INNER file (control), rename of it changes nothing;
  - after A unshares V, V's version POST on the file answers 404 (it answered something else before, so the file became
    invisible, not just unlisted), V can no longer view it by the app's own policy check, and V's rename changes nothing.
The rule being tested is the one written in apps/drive/policy.py (viewer: view and download; editor: also rename, move,
trash, restore, new version; share and unshare: owner only). Limits: move, restore and the commenter role are not exercised; the file upload bytes path is not used (version rows only); one file per case;
test client without CSRF; "changes" means any row field, not which row; one machine, one run; not an authorization proof."""

import pytest
from django.apps import apps as django_apps
from django.test import Client

from apps.accounts.models import CustomUser
from apps.core.foundation.permissions import can
from apps.drive import services as ds
from apps.drive.models import Share

HOST = "app.salowtech.com"
MARK = "ZqShared"
INNER = "ZqInnerFile"
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


def _changed(before, after):
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


@pytest.fixture
def world(db):
    a, v, e, w, c = _u("DS-A"), _u("DS-V"), _u("DS-E"), _u("DS-W"), _u("DS-C")
    f = ds.create_file(a, f"{MARK} file.txt")
    ds.share(a, f, v, "viewer")
    ds.share(a, f, e, "editor")
    top = ds.create_folder(a, f"{MARK} folder")
    inner = ds.create_file(a, f"{INNER}.txt", top)
    ds.share(a, top, w, "viewer")
    return {"a": a, "v": v, "e": e, "w": w, "c": c, "f": f, "top": top, "inner": inner}


def _client(user, n):
    cl = Client(HTTP_HOST=HOST, REMOTE_ADDR=f"10.4.0.{n}")
    cl.force_login(user)
    return cl


def _post(user, n, url, data):
    r = _client(user, n).post(url, data)
    assert r.status_code < 500, (url, r.status_code)
    return r


def _paths(item, share_to="DS-C", unshare="DS-V"):
    return {
        "rename": (f"/drive/file/{item.public_id}/rename/", {"name": "ZqHijacked.txt"}),
        "trash": (f"/drive/file/{item.public_id}/trash/", {}),
        "share": (f"/drive/file/{item.public_id}/share/", {"employee_id": share_to, "role": "viewer"}),
        "unshare": (f"/drive/file/{item.public_id}/unshare/", {"employee_id": unshare}),
        "version": (f"/drive/file/{item.public_id}/version/", {"base_version": 0, "size": 5, "sha256": "a" * 64}),
    }  # form fields exactly as apps/drive/views.py file_version reads them


def test_viewer_can_see_but_not_change_a_directly_shared_file(world):
    w = world
    assert MARK.encode() in _client(w["v"], 1).get("/drive/shared/").content, "control: viewer sees the shared file"
    paths = _paths(w["f"])
    for n, key in enumerate(("rename", "trash", "share", "unshare", "version"), start=2):
        before = _snapshot()
        r = _post(w["v"], n, *paths[key])
        assert not _changed(before, _snapshot()), f"viewer changed rows via {key} (status {r.status_code})"
    # the same payloads from the owner take effect, so the viewer's no-change is a refusal and not a bad payload
    _post(w["a"], 10, *paths["share"])
    assert Share.objects.filter(file=w["f"], user=w["c"], role="viewer").exists(), "owner share payload did not create the share"
    _post(w["a"], 11, *paths["version"])
    w["f"].refresh_from_db()
    assert w["f"].current_version == 1, "owner version payload did not record version 1"
    _post(w["a"], 12, *paths["unshare"])
    assert not Share.objects.filter(file=w["f"], user=w["v"]).exists(), "owner unshare payload did not remove V's share"


def test_editor_can_rename_and_trash_but_not_share_or_unshare(world):
    w = world
    paths = _paths(w["f"])  # unshare is aimed at V, another person, not at the editor leaving
    for n, key in enumerate(("share", "unshare"), start=2):
        before = _snapshot()
        r = _post(w["e"], n, *paths[key])
        assert not _changed(before, _snapshot()), f"editor changed rows via {key} (status {r.status_code})"
    assert Share.objects.filter(file=w["f"], user=w["v"]).exists() and not Share.objects.filter(file=w["f"], user=w["c"]).exists()
    _post(w["e"], 4, *paths["rename"])
    w["f"].refresh_from_db()
    assert w["f"].name == "ZqHijacked.txt", "editor rename did not set the name"
    _post(w["e"], 5, *paths["trash"])
    w["f"].refresh_from_db()
    assert w["f"].trashed_at is not None, "editor trash did not set trashed_at"


def test_viewer_by_folder_share_sees_the_inner_file_but_cannot_rename_it(world):
    w = world
    html = _client(w["w"], 1).get(f"/drive/folder/{w['top'].public_id}/").content.decode()
    assert INNER in html, "control: W's folder page lists the inner file"
    assert can(w["w"], "view", "drive.file", w["inner"])
    before = _snapshot()
    r = _post(w["w"], 2, *_paths(w["inner"])["rename"])
    assert not _changed(before, _snapshot()), f"folder viewer changed rows (status {r.status_code})"


def test_unshare_makes_the_file_invisible_to_the_unshared_viewer(world):
    w = world
    version = _paths(w["f"])["version"]
    before_status = _post(w["v"], 1, *version).status_code
    assert before_status != 404, "control: before unshare the file is visible to V (refusal is not a 404)"
    assert can(w["v"], "view", "drive.file", w["f"])
    before = _snapshot()
    _post(w["a"], 2, *_paths(w["f"])["unshare"])
    assert _changed(before, _snapshot()), "control: the owner's unshare changed nothing"
    assert not can(w["v"], "view", "drive.file", w["f"]), "V can still view by the app's policy"
    assert _post(w["v"], 3, *version).status_code == 404, "V's version POST is not a 404 after unshare"
    assert MARK.encode() not in _client(w["v"], 4).get("/drive/shared/").content
    before = _snapshot()
    _post(w["v"], 5, *_paths(w["f"])["rename"])
    assert not _changed(before, _snapshot()), "V changed rows after being unshared"
