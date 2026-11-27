import pytest
from django.test import Client

from apps.accounts.models import CustomUser
from apps.drive import services as svc
from apps.drive.models import DriveFile


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip


def _row(html, name):
    return html.split(name, 1)[1].split("</div>", 1)[0]


@pytest.mark.django_db
def test_trash_says_where_items_trashed_with_their_folder_were():
    a = _u("TC-A")
    top = svc.create_folder(a, "Parent box", None)
    sub = svc.create_folder(a, "Inner box", top)
    inside = DriveFile.objects.create(owner=a, folder=top, name="inside.txt", created_by=a)
    alone = DriveFile.objects.create(owner=a, folder=top, name="alone.txt", created_by=a)
    svc.trash(a, alone)  # trashed by itself first: its own batch
    svc.trash(a, top)
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(a)
    html = c.get("/drive/trash/").content.decode()
    assert "Was inside Parent box" in _row(html, "Inner box")
    assert "Was inside Parent box" in _row(html, "inside.txt")
    assert "Was inside" not in _row(html, "Parent box")  # the top of the batch has no parent line
    assert "Was inside" not in _row(html, "alone.txt")  # trashed on its own, a different batch
    assert html.count("Was inside Parent box") == 2
