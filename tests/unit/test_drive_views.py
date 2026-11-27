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
    assert b"Sending a file to someone else" in r.content
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
    ca.post(
        reverse("drive:unshare", args=["folder", top.public_id]), {"employee_id": b.employee_id}
    )
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


@pytest.mark.django_db
def test_breadcrumb_never_names_ancestors_the_viewer_cannot_open():
    a, b = _u("V-O"), _u("V-S")
    fin = svc.create_folder(a, "Finance")
    y = svc.create_folder(a, "2026", fin)
    q3 = svc.create_folder(a, "Q3", y)
    svc.share(a, q3, b, "viewer")
    cb = _client(b)
    html = cb.get(reverse("drive:folder", args=[q3.public_id])).content.decode()
    assert "Q3" in html and "Finance" not in html and "2026" not in html
    assert "Shared with me" in html
    assert cb.get(reverse("drive:folder", args=[y.public_id])).status_code == 404
    own = _client(a).get(reverse("drive:folder", args=[q3.public_id])).content.decode()
    assert "Finance" in own and "2026" in own


@pytest.mark.django_db
def test_share_with_unknown_or_inactive_person_and_bad_unshare_id():
    a, b = _u("V-P"), _u("V-Q")
    top = svc.create_folder(a, "Plans")
    ca = _client(a)
    url = reverse("drive:share", args=["folder", top.public_id])
    msgs = []
    for eid in ("NOPE-1", "V-Q"):
        if eid == "V-Q":
            CustomUser.objects.filter(pk=b.pk).update(is_active=False, status="inactive")
        r = ca.post(url, {"employee_id": eid, "role": "viewer"}, follow=True)
        msgs.append([str(m) for m in r.context["messages"]])
    assert msgs[0] == msgs[1] == ["No employee with that ID."]
    assert not Share.objects.exists()
    bad = reverse("drive:unshare", args=["folder", top.public_id])
    # unknown person and a real person with no share look the same (no existence probe)
    outs = []
    for data in ({"employee_id": "abc"}, {}, {"user_id": "1"}, {"employee_id": "V-P"}):
        r = ca.post(bad, data, follow=True)
        outs.append((r.redirect_chain[-1][0], [str(m) for m in r.context["messages"]]))
    assert len({str(o) for o in outs}) == 1


@pytest.mark.django_db
def test_share_actions_are_throttled_per_person():
    from apps.drive import views

    a, b = _u("V-T1"), _u("V-T2")
    top = svc.create_folder(a, "Busy")
    ca = _client(a)
    url = reverse("drive:share", args=["folder", top.public_id])
    codes = [
        ca.post(url, {"employee_id": "V-T2", "role": "viewer"}).status_code
        for _ in range(views.SHARE_LIMIT + 3)
    ]
    assert codes[: views.SHARE_LIMIT] == [302] * views.SHARE_LIMIT
    assert codes[views.SHARE_LIMIT :] == [429] * 3
    # another person is not affected
    cb = _client(b)
    assert cb.get(reverse("drive:home")).status_code == 200


@pytest.mark.django_db
def test_top_level_shared_folder_trail_starts_at_shared_with_me():
    a, b = _u("V-R1"), _u("V-R2")
    top = svc.create_folder(a, "Top shared")
    svc.share(a, top, b, "viewer")
    html = _client(b).get(reverse("drive:folder", args=[top.public_id])).content.decode()
    assert "Shared with me" in html and 'class="crumbs"' in html
    assert ">My Drive</a>" not in html.split('class="crumbs"')[1].split("</div>")[0]


@pytest.mark.django_db
def test_search_returns_only_owned_and_shared_items_and_share_notifies():
    from apps.core.foundation.search import search
    from apps.core.models import Notification

    a, b, c = _u("V-S1"), _u("V-S2"), _u("V-S3")
    fin = svc.create_folder(a, "Budget 2026")
    sub = svc.create_folder(a, "Budget details", fin)
    other = svc.create_folder(a, "Budget private")
    mine = svc.create_folder(b, "Budget mine")
    svc.share(a, fin, b, "viewer")
    names = {h.title for h in search(b, "budget")}
    assert names == {"Budget 2026", "Budget details", "Budget mine"}
    assert search(c, "budget") == []
    svc.trash(a, sub)
    assert {h.title for h in search(b, "budget")} == {"Budget 2026", "Budget mine"}
    import re

    uuid_url = re.compile(r"^/drive/folder/[0-9a-f-]{36}/$")
    assert all(uuid_url.match(h.url) for h in search(b, "budget"))
    n = Notification.objects.get(user=b, kind="drive.share")
    assert n.link == f"/drive/folder/{fin.public_id}/" and "Budget" not in n.title
    assert not Notification.objects.filter(user=c).exists()
    assert other.pk and mine.pk


@pytest.mark.django_db
def test_search_file_direct_share_and_trashed_owner_cases():
    from apps.core.foundation.search import search

    a, b = _u("V-F1"), _u("V-F2")
    top = svc.create_folder(a, "Vault")
    f = svc.create_file(a, "report plan.txt", top)
    svc.share(a, f, b, "viewer")
    hits = search(b, "report")
    assert [h.title for h in hits] == ["report plan.txt"]
    assert hits[0].url == "/drive/shared/" and str(top.public_id) not in hits[0].url
    own = search(a, "report")
    assert own[0].url == f"/drive/folder/{top.public_id}/"
    svc.trash(a, top)
    assert search(a, "report") == [] and search(a, "vault") == []
    assert search(b, "report") == []
