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
def test_slides_page_is_workspace_host_only_signed_in_only_and_scoped_per_person():
    a, b = _u("SL-1"), _u("SL-2")
    assert Client(HTTP_HOST=HOST).get("/slides/").status_code == 302
    ca = Client(HTTP_HOST=HOST)
    ca.force_login(a)
    r = ca.get("/slides/")
    html = r.content.decode()
    assert r.status_code == 200 and f'data-store-key="salow.deck.{a.pk}"' in html
    assert "Kept in this browser only" in html and "not encrypted" in html
    assert "no-store" in r["Cache-Control"]
    assert 'href="/slides/"' in ca.get("/chat/").content.decode()
    cb = Client(HTTP_HOST=HOST)
    cb.force_login(b)
    assert f"salow.deck.{a.pk}" not in cb.get("/slides/").content.decode()
    assert ca.get("/slides/", HTTP_HOST="iam.salowtech.com").status_code == 404


def test_slides_model_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the slides model table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/slides_model_table.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "223/223 slides model rows passed" in r.stdout


@pytest.mark.django_db
def test_slides_page_loads_images_js_before_editor_js_and_serves_it_on_the_workspace_host():
    c = Client(HTTP_HOST=HOST)
    c.force_login(_u("SL-3"))
    html = c.get("/slides/").content.decode()
    assert 0 < html.index("/static/slides/images.js") < html.index("/static/slides/editor.js")
    assert (ROOT / "static/slides/images.js").is_file()


def test_slides_layout_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the slides layout table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/slides_layout_table.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "56/56 layout rows passed" in r.stdout


def test_slides_tspeed_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the slides transition speed table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/slides_tspeed.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "14/14 tspeed rows passed" in r.stdout
