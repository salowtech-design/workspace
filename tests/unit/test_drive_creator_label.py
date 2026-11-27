import pytest
from django.test import Client

from apps.accounts.models import CustomUser
from apps.drive import services as svc
from apps.drive.models import DriveFile


def _u(eid, name):
    return CustomUser.objects.create(
        employee_id=eid, full_name=name, email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip


def _page(u, folder):
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(u)
    return c.get(f"/drive/folder/{folder.public_id}/")


@pytest.mark.django_db
def test_rows_show_who_created_them_to_other_viewers_only():
    a, b, o = _u("DC-A", "Owner Alpha"), _u("DC-B", "Editor Bravo"), _u("DC-O", "Outsider Oscar")
    top = svc.create_folder(a, "Joint", None)
    svc.share(a, top, b, "editor")
    svc.create_folder(b, "Made by bravo", top)
    DriveFile.objects.create(owner=a, folder=top, name="bravo.txt", created_by=b)
    DriveFile.objects.create(owner=a, folder=top, name="alpha.txt", created_by=None)
    owner_view = _page(a, top).content.decode()
    assert owner_view.count("Created by Editor Bravo") == 2  # the folder row and the file row
    assert "Created by Owner Alpha" not in owner_view
    bravo_view = _page(b, top).content.decode()
    assert "Created by Editor Bravo" not in bravo_view  # your own items carry no label
    assert "Made by bravo" in bravo_view
    assert "Created by" not in _page(a, top).content.decode().split("alpha.txt")[1].split("</div>")[0]  # unknown creator: no line
    out = _page(o, top)
    assert out.status_code == 404 and "Editor Bravo" not in out.content.decode()
