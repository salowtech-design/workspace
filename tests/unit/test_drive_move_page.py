"""Moving an item through the page: the service rules (owner, destination access, no cycles) hold through the view."""
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.drive import services as svc
from apps.drive.models import DriveFile, Folder


def _u(eid):
    return CustomUser.objects.create(employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com", role="employee", status="active", is_active=True)


def _c(user):
    c = Client()
    c.force_login(user)
    return c


def _digest(resp):
    import re

    return re.search(r'name="digest" value="([0-9a-f]+)"', resp.content.decode()).group(1)


def _move(client, folder, dest):
    return client.post(reverse("drive:move", args=["folder", folder.public_id]), {"destination": dest}, follow=True)


@pytest.mark.django_db
def test_owner_moves_a_folder_into_another_and_back_to_top():
    a = _u("MV-A")
    one, two = svc.create_folder(a, "One"), svc.create_folder(a, "Two")
    r = _move(_c(a), one, str(two.public_id))
    one.refresh_from_db()
    assert one.parent_id == two.pk and b"Moved." in r.content
    _move(_c(a), one, "")
    one.refresh_from_db()
    assert one.parent_id is None


@pytest.mark.django_db
def test_the_page_offers_a_move_choice_listing_own_folders():
    a = _u("MV-B")
    svc.create_folder(a, "Alpha")
    svc.create_folder(a, "Beta")
    html = _c(a).get(reverse("drive:home")).content.decode()
    assert 'aria-label="Move to"' in html and ">Beta</option>" in html and "Top level" in html


@pytest.mark.django_db
def test_a_stranger_gets_404_and_nothing_moves():
    a, b = _u("MV-C"), _u("MV-D")
    one, two = svc.create_folder(a, "One"), svc.create_folder(a, "Two")
    assert _c(b).post(reverse("drive:move", args=["folder", one.public_id]), {"destination": str(two.public_id)}).status_code == 404
    one.refresh_from_db()
    assert one.parent_id is None


@pytest.mark.django_db
def test_a_viewer_of_a_nested_folder_cannot_change_its_parent():
    a, v = _u("MV-E"), _u("MV-F")
    top, other = svc.create_folder(a, "Top"), svc.create_folder(a, "Other")
    nested = svc.create_folder(a, "Nested", parent=top)
    svc.share(a, top, v, "viewer")  # the viewer reaches Nested only through Top
    svc.share(a, other, v, "viewer")  # and can see the destination, so the refusal is the move rule itself
    assert nested.parent_id == top.pk
    r = _move(_c(v), nested, str(other.public_id))
    nested.refresh_from_db()
    assert nested.parent_id == top.pk
    assert b"You do not have permission to do that." in r.content and b"Moved." not in r.content


@pytest.mark.django_db
def test_a_folder_cannot_enter_itself_or_its_child():
    a = _u("MV-I")
    one = svc.create_folder(a, "One")
    child = svc.create_folder(a, "Child", parent=one)
    for dest in (one, child):
        r = _move(_c(a), one, str(dest.public_id))
        one.refresh_from_db()
        assert one.parent_id is None and b"into itself" in r.content


@pytest.mark.django_db
def test_file_owner_moves_a_file_and_a_stranger_gets_404():
    a, b = _u("MV-J"), _u("MV-K")
    dest = svc.create_folder(a, "Dest")
    f = DriveFile.objects.create(owner=a, name="plan.txt", created_by=a)
    url = reverse("drive:move", args=["file", f.public_id])
    assert _c(b).post(url, {"destination": str(dest.public_id)}).status_code == 404
    f.refresh_from_db()
    assert f.folder_id is None
    r = _c(a).post(url, {"destination": str(dest.public_id)}, follow=True)
    f.refresh_from_db()
    assert f.folder_id == dest.pk and b"Moved." in r.content


@pytest.mark.django_db
def test_trashed_destination_and_name_collision_are_refused():
    a = _u("MV-L")
    one, gone, dest = svc.create_folder(a, "One"), svc.create_folder(a, "Gone"), svc.create_folder(a, "Dest")
    svc.create_folder(a, "One", parent=dest)  # same name already inside Dest
    svc.trash(a, gone)
    r = _move(_c(a), one, str(gone.public_id))
    one.refresh_from_db()
    assert one.parent_id is None and b"Moved." not in r.content
    r = _move(_c(a), one, str(dest.public_id))
    one.refresh_from_db()
    assert one.parent_id is None and b"already used in the destination" in r.content


@pytest.mark.django_db
def test_move_to_the_place_it_already_is_changes_nothing():
    a = _u("MV-M")
    dest = svc.create_folder(a, "Dest")
    one = svc.create_folder(a, "One", parent=dest)
    r = _move(_c(a), one, str(dest.public_id))
    one.refresh_from_db()
    assert one.parent_id == dest.pk and r.status_code == 200


@pytest.mark.django_db
def test_move_into_a_shared_folder_asks_first_names_who_gains_and_moves_only_on_confirm():
    from apps.core.foundation.permissions import can

    a, v = _u("MV-N"), _u("MV-O")
    shared, private = svc.create_folder(a, "Shared box"), svc.create_folder(a, "Private box")
    svc.share(a, shared, v, "viewer")
    one = svc.create_folder(a, "One", parent=private)
    assert not can(v, "view", "drive.folder", one)
    html = _c(a).get(reverse("drive:folder", args=[private.public_id])).content.decode()
    assert "Shared box (shared)" in html and "Private box (shared)" not in html and "Moving an item changes who can open it" in html
    url = reverse("drive:move", args=["folder", one.public_id])
    r = _c(a).post(url, {"destination": str(shared.public_id)})  # first press: only the question
    one.refresh_from_db()
    body = r.content.decode()
    assert one.parent_id == private.pk and not can(v, "view", "drive.folder", one)
    assert "Confirm move" in body and "Person MV-O (MV-O) gets access as viewer" in body and "Shared box" in body
    r = _c(a).post(url, {"destination": str(shared.public_id), "confirm": "1", "digest": _digest(r)}, follow=True)
    one.refresh_from_db()
    assert one.parent_id == shared.pk and b"Moved." in r.content
    assert can(v, "view", "drive.folder", one)


@pytest.mark.django_db
def test_a_move_that_only_narrows_access_does_not_ask():
    a, v = _u("MV-R"), _u("MV-S")
    shared, private = svc.create_folder(a, "Shared box"), svc.create_folder(a, "Private box")
    svc.share(a, shared, v, "viewer")
    one = svc.create_folder(a, "One", parent=shared)
    gain, lose = svc.move_audience(one, private)
    assert gain == [] and [(u.employee_id, old, new) for u, old, new in lose] == [("MV-S", "viewer", None)]
    r = _move(_c(a), one, str(private.public_id))
    one.refresh_from_db()
    assert one.parent_id == private.pk and b"Moved." in r.content


@pytest.mark.django_db
def test_a_stronger_role_at_the_destination_counts_as_gain_and_direct_shares_stay():
    a, v = _u("MV-T"), _u("MV-U")
    src, dst = svc.create_folder(a, "Src"), svc.create_folder(a, "Dst")
    svc.share(a, src, v, "viewer")
    svc.share(a, dst, v, "editor")
    one = svc.create_folder(a, "One", parent=src)
    gain, lose = svc.move_audience(one, dst)
    assert [(u.employee_id, new, old) for u, new, old in gain] == [("MV-U", "editor", "viewer")] and lose == []
    svc.share(a, one, v, "editor")  # a direct share on the item itself stays whatever the destination
    assert svc.move_audience(one, svc.create_folder(a, "Plain")) == ([], [])


@pytest.mark.django_db
def test_file_move_audience_counts_the_destination_shares_and_keeps_the_files_own_share():
    a, v, w = _u("MV-V"), _u("MV-W"), _u("MV-X")
    dest = svc.create_folder(a, "Dest")
    svc.share(a, dest, w, "editor")
    f = DriveFile.objects.create(owner=a, name="plan.txt", created_by=a)
    svc.share(a, f, v, "viewer")  # shared directly with v
    gain, lose = svc.move_audience(f, dest)
    assert [(u.employee_id, new) for u, new, _ in gain] == [("MV-X", "editor")] and lose == []
    url = reverse("drive:move", args=["file", f.public_id])
    r = _c(a).post(url, {"destination": str(dest.public_id)})
    f.refresh_from_db()
    assert f.folder_id is None and b"Person MV-X (MV-X) gets access as editor" in r.content
    _c(a).post(url, {"destination": str(dest.public_id), "confirm": "1", "digest": _digest(r)})
    f.refresh_from_db()
    assert f.folder_id == dest.pk and v.drive_shares.filter(file=f).exists()


@pytest.mark.django_db
def test_a_non_owner_who_may_move_sees_only_how_many_people_gain_access():
    a, e, v = _u("MV-Y"), _u("MV-Z"), _u("MV-AA")
    src, dst = svc.create_folder(a, "Src"), svc.create_folder(a, "Dst")
    svc.share(a, src, e, "editor")
    svc.share(a, dst, e, "editor")
    svc.share(a, dst, v, "viewer")
    one = svc.create_folder(a, "One", parent=src)
    r = _c(e).post(reverse("drive:move", args=["folder", one.public_id]), {"destination": str(dst.public_id)})
    body = r.content.decode()
    one.refresh_from_db()
    assert one.parent_id == src.pk and "Confirm move" in body
    assert "1 person gets new or stronger access." in body and "MV-AA" not in body


@pytest.mark.django_db
def test_move_lists_are_capped_and_say_so():
    from apps.drive.views import MOVE_TARGET_MAX

    a = _u("MV-P")
    for i in range(MOVE_TARGET_MAX + 3):
        svc.create_folder(a, "F%03d" % i)
    html = _c(a).get(reverse("drive:home")).content.decode()
    first_row = html.split('aria-label="Move to"')[1].split("</select>")[0]
    assert first_row.count("<option") == MOVE_TARGET_MAX  # Top level plus the listed folders, minus the row's own folder
    assert "show only the first 50 folders" in html


@pytest.mark.django_db
def test_unknown_or_malformed_destination_and_another_owners_folder_are_refused():
    a, b = _u("MV-G"), _u("MV-H")
    one, theirs = svc.create_folder(a, "One"), svc.create_folder(b, "Theirs")
    for dest in (str(uuid.uuid4()), "not-a-uuid", str(theirs.public_id)):
        r = _move(_c(a), one, dest)
        one.refresh_from_db()
        assert one.parent_id is None and b"Moved." not in r.content, dest


@pytest.mark.django_db
def test_a_file_row_lists_every_folder_even_when_a_file_and_folder_share_a_number():
    a = _u("MV-Q")
    folder = svc.create_folder(a, "Same number")
    DriveFile.objects.create(pk=folder.pk, owner=a, name="same.txt", created_by=a)
    html = _c(a).get(reverse("drive:home")).content.decode()
    import re

    m = re.search(r'/file/[0-9a-f-]+/move/.*?</select>', html, re.S)
    assert m, "the file row has no Move control"
    file_form = m.group(0)
    assert ">Same number</option>" in file_form


@pytest.mark.django_db
def test_sharing_that_changes_between_the_two_presses_needs_a_fresh_confirmation():
    a, v, w = _u("MV-AB"), _u("MV-AC"), _u("MV-AD")
    dst = svc.create_folder(a, "Dst")
    svc.share(a, dst, v, "viewer")
    one = svc.create_folder(a, "One")
    url = reverse("drive:move", args=["folder", one.public_id])
    first = _c(a).post(url, {"destination": str(dst.public_id)})
    d1 = _digest(first)
    svc.share(a, dst, w, "editor")  # someone else gets the destination between the two presses
    r = _c(a).post(url, {"destination": str(dst.public_id), "confirm": "1", "digest": d1})
    one.refresh_from_db()
    body = r.content.decode()
    assert one.parent_id is None and "The sharing changed since you last looked" in body
    assert "MV-AD (MV-AD) gets access as editor" in body and _digest(r) != d1
    for bad in ({}, {"digest": "0" * 32}, {"digest": "forged"}):  # missing or forged digest
        r2 = _c(a).post(url, {"destination": str(dst.public_id), "confirm": "1", **bad})
        one.refresh_from_db()
        assert one.parent_id is None and "Confirm move" in r2.content.decode()
    _c(a).post(url, {"destination": str(dst.public_id), "confirm": "1", "digest": _digest(r)})
    one.refresh_from_db()
    assert one.parent_id == dst.pk


@pytest.mark.django_db
def test_an_invalid_move_is_refused_before_any_confirmation_is_shown():
    a, v = _u("MV-AE"), _u("MV-AF")
    dst = svc.create_folder(a, "Dst")
    svc.share(a, dst, v, "viewer")
    svc.create_folder(a, "One", parent=dst)  # name already used in Dst
    one = svc.create_folder(a, "One")
    r = _c(a).post(reverse("drive:move", args=["folder", one.public_id]), {"destination": str(dst.public_id)}, follow=True)
    one.refresh_from_db()
    assert one.parent_id is None and b"already used in the destination" in r.content and b"Confirm move" not in r.content
    child = svc.create_folder(a, "Child", parent=one)
    svc.share(a, child, v, "viewer")
    r = _c(a).post(reverse("drive:move", args=["folder", one.public_id]), {"destination": str(child.public_id)}, follow=True)
    assert b"into itself" in r.content and b"Confirm move" not in r.content


@pytest.mark.django_db
def test_a_folder_past_the_first_fifty_is_found_by_name_and_still_goes_through_confirmation():
    from apps.drive.views import MOVE_TARGET_MAX, _move_targets

    a, v, o = _u("MV-FA"), _u("MV-FB"), _u("MV-FC")
    for i in range(MOVE_TARGET_MAX + 5):
        svc.create_folder(a, "Dest%03d" % i)
    last = svc.create_folder(a, "Dest%03d" % (MOVE_TARGET_MAX + 5))
    svc.share(a, last, v, "viewer")  # the last by name is shared, so moving there widens access
    listed, more = _move_targets(a)
    assert more and last.pk not in [t.pk for t in listed]  # the plain Move list cannot reach it
    one = svc.create_folder(a, "One")
    find = reverse("drive:move_find", args=["folder", one.public_id])
    r = _c(a).get(find, {"q": "dest055"})
    body = r.content.decode()
    assert r.status_code == 200 and "Dest055 (shared)" in body and "Dest054" not in body
    assert str(last.public_id) in body and "Move here" in body
    r = _move(_c(a), one, str(last.public_id))  # what the Move here button posts
    one.refresh_from_db()
    assert one.parent_id is None and "Confirm move" in r.content.decode()  # the audience check still runs
    many = _c(a).get(find, {"q": "Dest"}).content.decode()  # 56 matches: capped at 50, and it says so
    assert many.count("Move here") == MOVE_TARGET_MAX and "Only the first 50 matches are shown" in many
    assert "Only the first" not in _c(a).get(find, {"q": "dest055"}).content.decode()
    assert b"No folder of yours" in _c(a).get(find, {"q": "zzz"}).content
    assert b"Move here" not in _c(a).get(find).content  # no query, no list
    # a search never shows someone else's folder, a trashed one, or the item itself
    theirs = svc.create_folder(o, "Dest-theirs")
    gone = svc.create_folder(a, "Dest-gone")
    svc.trash(a, gone)
    seen = _c(a).get(find, {"q": "Dest-"}).content.decode()
    assert "Dest-theirs" not in seen and "Dest-gone" not in seen and str(theirs.public_id) not in seen
    assert 'name="destination" value="%s"' % one.public_id not in _c(a).get(find, {"q": "One"}).content.decode()
    # the page is read-only and refuses an item the person cannot see
    assert _c(o).get(find, {"q": "Dest"}).status_code == 404
    assert one.parent_id is None
