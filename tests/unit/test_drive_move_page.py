"""Moving a folder through the page: the service rules (owner, destination access, no cycles) hold through the view."""
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.drive import services as svc
from apps.drive.models import Folder


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
def test_a_viewer_cannot_move_and_a_folder_cannot_enter_itself_or_its_child():
    a, v = _u("MV-E"), _u("MV-F")
    one = svc.create_folder(a, "One")
    child = svc.create_folder(a, "Child", parent=one)
    svc.share(a, one, v, "viewer")
    r = _move(_c(v), one, "")
    one.refresh_from_db()
    assert one.parent_id is None and b"permission" in r.content.lower()
    r = _move(_c(a), one, str(one.public_id))
    one.refresh_from_db()
    assert one.parent_id is None and b"into itself" in r.content
    if True:
        r = _move(_c(a), one, str(child.public_id))
        one.refresh_from_db()
        assert one.parent_id is None and b"into itself" in r.content


@pytest.mark.django_db
def test_unknown_or_malformed_destination_and_another_owners_folder_are_refused():
    a, b = _u("MV-G"), _u("MV-H")
    one, theirs = svc.create_folder(a, "One"), svc.create_folder(b, "Theirs")
    for dest in (str(uuid.uuid4()), "not-a-uuid", str(theirs.public_id)):
        r = _move(_c(a), one, dest)
        one.refresh_from_db()
        assert one.parent_id is None and b"Moved." not in r.content, dest
