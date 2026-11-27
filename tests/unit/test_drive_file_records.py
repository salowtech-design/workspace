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


@pytest.mark.django_db
def test_column_bounds_zero_size_negative_base_and_mime_claims():
    a = _u("FR-F")
    c = _c(a)
    fid = _new(c).json()["file"]["id"]
    assert _ver(c, fid, -1).status_code == 400
    assert _ver(c, fid, 0, 2**63).status_code == 400 and _ver(c, fid, 0, 2**64).status_code == 400
    r = _ver(c, fid, 0, 2**63 - 1)  # the largest the column holds is stored exactly
    assert r.status_code == 200 and DriveFile.objects.get(public_id=fid).size == 2**63 - 1
    r = _ver(c, fid, 1, 0)  # an empty file is a real version
    assert r.status_code == 200 and r.json()["file"]["size"] == 0 and r.json()["file"]["version"] == 2
    keys = list(FileVersion.objects.filter(file__public_id=fid).values_list("storage_key", flat=True))
    assert len(keys) == 2 and len(set(keys)) == 2
    # the claimed type is syntax checked only: no spaces, no control characters, no missing subtype
    for bad in ("text/html; x", "nope", "a/b\nc", "text/ht ml", "/x", "x/"):
        assert c.post(reverse("drive:file_new"), {"name": f"m{len(bad)}.x", "content_type": bad}).status_code == 400
    ok = c.post(reverse("drive:file_new"), {"name": "ok.svg", "content_type": "Image/SVG+XML"})
    assert ok.status_code == 201 and ok.json()["file"]["content_type"] == "image/svg+xml"
    # names: control characters and over-long are refused
    assert _new(c, "a\x00b").status_code == 400 and _new(c, "n" * 201).status_code == 400
    assert _new(c, "n" * 200).status_code == 201


@pytest.mark.django_db
def test_returned_version_is_what_this_call_committed_and_trash_blocks_writes():
    a = _u("FR-G")
    c = _c(a)
    fid = _new(c).json()["file"]["id"]
    f = DriveFile.objects.get(public_id=fid)
    # someone else's later save must not be reported as ours
    real = svc.add_version
    calls = []

    def racing(user, file, base, size, sha):
        v = real(user, file, base, size, sha)
        calls.append(v.number)
        DriveFile.objects.filter(pk=f.pk).update(current_version=5, size=999)  # a later writer
        return v

    import apps.drive.views as dv

    dv.svc.add_version = racing
    try:
        r = _ver(c, fid, 0, 7)
    finally:
        dv.svc.add_version = real
    assert r.json()["file"]["version"] == 1 and r.json()["file"]["size"] == 7 and calls == [1]
    # a trashed file and a file in a trashed folder take no new version and no new file
    DriveFile.objects.filter(pk=f.pk).update(current_version=1)
    folder = svc.create_folder(a, "Gone")
    inside = _new(c, "i.pdf", folder).json()["file"]["id"]
    svc.trash(a, folder)
    assert _ver(c, inside, 0).status_code == 403  # policy refuses edits in the trash, even for the owner
    assert _new(c, "j.pdf", folder).status_code == 403
    assert not FileVersion.objects.filter(file__public_id=inside).exists()
    svc.trash(a, DriveFile.objects.get(pk=f.pk))
    assert _ver(c, fid, 1).status_code == 403


@pytest.mark.django_db
def test_csrf_is_enforced_and_get_is_405():
    a = _u("FR-H")
    c = Client(enforce_csrf_checks=True)
    c.force_login(a)
    assert c.post(reverse("drive:file_new"), {"name": "x.pdf"}).status_code == 403  # no token
    assert DriveFile.objects.filter(owner=a).count() == 0
    c.get(reverse("drive:home"))  # sets the csrftoken cookie
    tok = c.cookies["csrftoken"].value
    r = c.post(reverse("drive:file_new"), {"name": "x.pdf"}, HTTP_X_CSRFTOKEN=tok)
    assert r.status_code == 201
    fid = r.json()["file"]["id"]
    assert c.post(reverse("drive:file_version", args=[fid]), {"base_version": 0, "size": 1, "sha256": H}).status_code == 403
    assert c.get(reverse("drive:file_new")).status_code == 405
    assert c.get(reverse("drive:file_version", args=[fid])).status_code == 405


@pytest.mark.django_db
def test_page_carries_the_checksum_only_for_saved_versions_escapes_names_and_names_the_store_after_the_person():
    a = _u("FR-P")
    c = _c(a)
    f0 = _new(c, 'zero"><b>x.pdf').json()["file"]
    f1 = _new(c, "one.pdf").json()["file"]
    _ver(c, f1["id"], 0, 5)
    html = c.get(reverse("drive:home")).content.decode()
    assert f'data-user="{a.pk}"' in html and "salow-drive" not in html.split("<script")[0]
    assert 'data-sha="' + H + '"' in html and html.count('data-sha=""') == 1  # version 0 has no checksum
    assert "No file saved yet" in html and "<b>x" not in html and "&lt;b&gt;x" in html
    other = _u("FR-Q")
    assert H not in _c(other).get(reverse("drive:home")).content.decode()


@pytest.mark.django_db
def test_page_exposes_only_the_current_checksum_to_a_viewer_and_a_stranger_gets_nothing():
    a, v, s = _u("FR-R"), _u("FR-S"), _u("FR-T")
    ca, cv, cs = _c(a), _c(v), _c(s)
    folder = svc.create_folder(a, "Shared team")
    fid = _new(ca, "plan.pdf", folder).json()["file"]["id"]
    old, new = "1" * 64, "2" * 64
    assert _ver(ca, fid, 0, 4, old).status_code == 200 and _ver(ca, fid, 1, 6, new).status_code == 200
    svc.share(a, folder, v, "viewer")
    url = reverse("drive:folder", args=[folder.public_id])
    owner_html = ca.get(url).content.decode()
    viewer_html = cv.get(url).content.decode()
    for html in (owner_html, viewer_html):
        assert f'data-sha="{new}"' in html and old not in html  # only the current version's checksum is ever in the page
    assert "drive-reattach" in viewer_html and "drive-newver" not in viewer_html  # a viewer can put a copy back but not add a version
    assert "drive-newver" in owner_html
    assert cs.get(url).status_code == 404 and new not in cs.get(reverse("drive:home")).content.decode()
    # the lookup is one filtered query for the whole page, not one per file and not every version
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for i in range(4):
        x = _new(ca, f"f{i}.pdf", folder).json()["file"]["id"]
        _ver(ca, x, 0, 1, f"{i + 3}" * 64)
    with CaptureQueriesContext(connection) as q:
        ca.get(url)
    ver_q = [x for x in q.captured_queries if "drive_fileversion" in x["sql"].lower()]
    assert len(ver_q) == 1 and "current_version" in ver_q[0]["sql"].lower()
