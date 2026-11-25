import shutil
import subprocess
from pathlib import Path

import pytest
from django.test import Client

from apps.accounts.models import CustomUser

HOST = "app.salowtech.com"
ROOT = Path(__file__).resolve().parents[2]


def _u(eid):
    return CustomUser.objects.create_user(
        employee_id=eid, full_name=f"P {eid}", password="ValidStrongPassword123!",
        role="employee", status="active",
    )  # fmt: skip


@pytest.mark.django_db
def test_pdf_page_is_workspace_host_only_signed_in_only_and_says_what_it_does():
    a = _u("PD-1")
    assert Client(HTTP_HOST=HOST).get("/pdf/").status_code == 302
    ca = Client(HTTP_HOST=HOST)
    ca.force_login(a)
    r = ca.get("/pdf/")
    html = r.content.decode()
    assert r.status_code == 200 and "not uploaded" in html and "not built yet" in html
    assert "no-store" in r["Cache-Control"]
    assert 'href="/pdf/"' in ca.get("/chat/").content.decode()
    assert ca.get("/pdf/", HTTP_HOST="iam.salowtech.com").status_code == 404


def test_pdf_check_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the pdf check table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/pdf_check_table.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "9/9 pdf check rows passed" in r.stdout
