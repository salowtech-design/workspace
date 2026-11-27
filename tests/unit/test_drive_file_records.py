"""Drive file RECORD endpoints: metadata only, the same permissions as the rest of Drive."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.drive import services as svc
from apps.drive.models import DriveFile, FileVersion

H = "a" * 64


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )


def _c(u):
    c = Client()
    c.force_login(u)
    return c


def _new(c, name="a.pdf", folder=None):
    d = {"name": name, "content_type": "application/pdf"}
    if folder is not None:
        d["folder"] = str(folder.public_id)
    return c.post(reverse("drive:file_new"), d)


def _ver(c, fid, base, size=10, sha=H):
    return c.post(reverse("drive:file_version", args=[fid]), {"base_version": base, "size": size, "sha256": sha})


@pytest.mark.django_db
def test_create_then_version_records_metadata_only_and_login_is_required():
    a = _u("FR-A")
    r = _new(_c(a))
    assert r.status_code == 201 and r.json()["file"]["version"] == 0 and r.json()["file"]["size"] == 0
    fid = r.json()["file"]["id"]
    r = _ver(_c(a), fid, 0, 123)
    assert r.status_code == 200 and r.json()["file"]["version"] == 1 and r.json()["file"]["size"] == 123
    v = FileVersion.objects.get(file__public_id=fid)
    assert v.sha256 == H and v.size == 123 and len(v.storage_key) == 64
    anon = Client()
    assert anon.post(reverse("drive:file_new"), {"name": "x"}).status_code == 302
    assert anon.post(reverse("drive:file_version", args=[fid]), {}).status_code == 302
    assert Client().get(reverse("drive:file_new"), follow=False).status_code in (302, 405)


@pytest.mark.django_db
def test_stale_base_is_409_and_bad_input_is_400_without_a_new_version():
    a = _u("FR-B")
    c = _c(a)
    fid = _new(c).json()["file"]["id"]
    assert _ver(c, fid, 0).status_code == 200
    r = _ver(c, fid, 0)  # saved from an old version
    assert r.status_code == 409 and r.json()["current_version"] == 1
    for kw in ({"sha": "xyz"}, {"sha": H.upper()}, {"size": -1}):
        assert _ver(c, fid, 1, **kw).status_code == 400
    assert c.post(reverse("drive:file_version", args=[fid]), {"base_version": "x", "size": 1, "sha256": H}).status_code == 400
    assert DriveFile.objects.get(public_id=fid).current_version == 1
    assert FileVersion.objects.filter(file__public_id=fid).count() == 1


@pytest.mark.django_db
def test_permissions_stranger_viewer_editor_and_duplicate_names():
    a, b, s = _u("FR-C"), _u("FR-D"), _u("FR-E")
    ca, cb, cs = _c(a), _c(b), _c(s)
    folder = svc.create_folder(a, "Team")
    fid = _new(ca, "doc.pdf", folder).json()["file"]["id"]
    # a stranger learns nothing: same 404 for the folder and the file
    assert _new(cs, "x.pdf", folder).status_code == 404
    assert _ver(cs, fid, 0).status_code == 404
    # a viewer can see it but cannot write
    svc.share(a, folder, b, "viewer")
    assert _new(cb, "y.pdf", folder).status_code == 403
    assert _ver(cb, fid, 0).status_code == 403
    assert not FileVersion.objects.filter(file__public_id=fid).exists()
    assert DriveFile.objects.filter(folder=folder).count() == 1
    # an editor can
    svc.share(a, folder, b, "editor")
    assert _new(cb, "y.pdf", folder).status_code == 201
    assert _ver(cb, fid, 0).status_code == 200
    # a name already used in the folder is refused
    assert _new(ca, "doc.pdf", folder).status_code == 400
    assert _new(ca, "").status_code == 400
    assert _new(ca, "z.pdf", None).status_code == 201
