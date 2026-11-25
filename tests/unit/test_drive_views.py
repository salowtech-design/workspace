"""Drive pages through the real Django views (test client, login by session)."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.drive import services as svc
from apps.drive.models import DriveFile, Folder, Share


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid,
        full_name=f"Person {eid}",
        email=f"{eid.lower()}@salowtech.com",
        role="employee",
        status="active",
        is_active=True,
    )


def _client(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_pages_render_and_nothing_leaks_between_people():
    a, b = _u("V-A"), _u("V-B")
    ca, cb = _client(a), _client(b)
    top = svc.create_folder(a, "Secret plans")
    r = ca.get(reverse("drive:home"))
    assert r.status_code == 200 and b"Secret plans" in r.content
    assert b"file storage is not set up" in r.content
    # the other person sees neither the list entry nor the folder page
    assert b"Secret plans" not in cb.get(reverse("drive:home")).content
    assert cb.get(reverse("drive:folder", args=[top.public_id])).status_code == 404
    assert cb.post(reverse("drive:trash_item", args=["folder", top.public_id])).status_code == 404
    assert Folder.objects.get(pk=top.pk).trashed_at is None


@pytest.mark.django_db
def test_share_edit_and_revoke_through_the_pages():
    a, b = _u("V-C"), _u("V-D")
    ca, cb = _client(a), _client(b)
    top = svc.create_folder(a, "Team")
    ca.post(
        reverse("drive:share", args=["folder", top.public_id]),
        {"employee_id": "v-d", "role": "editor"},
    )
    assert Share.objects.filter(folder=top, user=b, role="editor").exists()
    assert b"Team" in cb.get(reverse("drive:shared")).content
    cb.post(reverse("drive:new_folder"), {"name": "By D", "parent": top.public_id})
    made = Folder.objects.get(name="By D")
    assert made.owner_id == a.pk and made.created_by_id == b.pk
    # an editor cannot share on
    cb.post(
        reverse("drive:share", args=["folder", top.public_id]),
        {"employee_id": "V-C", "role": "viewer"},
    )
    assert Share.objects.filter(folder=top).count() == 1
    ca.post(reverse("drive:unshare", args=["folder", top.public_id]), {"user_id": b.pk})
    assert cb.get(reverse("drive:folder", args=[top.public_id])).status_code == 404
    assert b"Team" not in cb.get(reverse("drive:shared")).content


@pytest.mark.django_db
def test_trash_and_restore_pages_and_csrf_and_login_required():
    a = _u("V-E")
    top = svc.create_folder(a, "Old")
    f = DriveFile.objects.create(owner=a, folder=top, name="x.txt")
    ca = _client(a)
    ca.post(reverse("drive:trash_item", args=["folder", top.public_id]))
    assert b"Old" in ca.get(reverse("drive:trash")).content
    assert b"Old" not in ca.get(reverse("drive:home")).content
    ca.post(reverse("drive:restore_item", args=["folder", top.public_id]))
    f.refresh_from_db()
    assert f.trashed_at is None and b"Old" in ca.get(reverse("drive:home")).content
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(a)
    assert (
        strict.post(reverse("drive:trash_item", args=["folder", top.public_id])).status_code == 403
    )
    assert Client().get(reverse("drive:home")).status_code in (302, 401, 403)
    assert ca.get(reverse("drive:rename", args=["folder", top.public_id])).status_code == 405
    import uuid

    assert ca.post(f"/drive/nope/{uuid.uuid4()}/rename/").status_code == 404
    assert (
        ca.post(f"/drive/folder/{top.pk}/rename/").status_code == 404
    )  # integer ids are not routes
    assert ca.get(f"/drive/folder/{top.pk}/").status_code == 404
