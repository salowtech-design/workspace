"""Moving a folder through the page: the service rules (owner, destination access, no cycles) hold through the view."""
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
def test_move_changes_what_the_viewer_can_open_and_the_page_marks_shared_folders():
    from apps.core.foundation.permissions import can

    a, v = _u("MV-N"), _u("MV-O")
    shared, private = svc.create_folder(a, "Shared box"), svc.create_folder(a, "Private box")
    svc.share(a, shared, v, "viewer")
    one = svc.create_folder(a, "One", parent=private)
    assert not can(v, "view", "drive.folder", one)
    html = _c(a).get(reverse("drive:folder", args=[private.public_id])).content.decode()
    assert "Shared box (shared)" in html and "Private box (shared)" not in html and "takes on the sharing of the folder you move it into" in html
    _move(_c(a), one, str(shared.public_id))
    one.refresh_from_db()
    assert can(v, "view", "drive.folder", one)  # moving into a shared folder widened the audience


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
