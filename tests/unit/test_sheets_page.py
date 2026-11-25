import pytest
from django.test import Client

from apps.accounts.models import CustomUser

HOST = "app.salowtech.com"
pytestmark = pytest.mark.django_db


def _u(eid, status="active"):
    return CustomUser.objects.create_user(
        employee_id=eid, full_name=f"P {eid}", password="ValidStrongPassword123!",
        role="employee", status=status,
    )  # fmt: skip


def test_sheets_page_is_workspace_host_only_signed_in_only_and_scoped_per_person():
    a, b = _u("SH-1"), _u("SH-2")
    assert Client(HTTP_HOST=HOST).get("/sheets/").status_code == 302
    ca = Client(HTTP_HOST=HOST)
    ca.force_login(a)
    r = ca.get("/sheets/")
    html = r.content.decode()
    assert r.status_code == 200 and f'data-store-key="salow.sheet.{a.pk}"' in html
    assert (
        "kept in this browser only" in html
        and "not encrypted" in html
        and "no-store" in r["Cache-Control"]
    )
    assert 'href="/sheets/"' in ca.get("/chat/").content.decode()  # nav link
    cb = Client(HTTP_HOST=HOST)
    cb.force_login(b)
    assert f"salow.sheet.{a.pk}" not in cb.get("/sheets/").content.decode()
    assert ca.get("/sheets/", HTTP_HOST="iam.salowtech.com").status_code == 404
