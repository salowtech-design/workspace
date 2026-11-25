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
    assert r.status_code == 200 and "not uploaded" in html and "Password-protected" in html
    assert "no-store" in r["Cache-Control"]
    assert f'data-prefix="salow.pdfnotes.{a.pk}."' in html and "not written into the PDF" in html
    b = _u("PD-2")
    cb = Client(HTTP_HOST=HOST)
    cb.force_login(b)
    assert f"salow.pdfnotes.{a.pk}." not in cb.get("/pdf/").content.decode()
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
    assert "22/22 pdf check rows passed" in r.stdout


def test_pdf_notes_table_passes_in_node():
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/pdf_notes_table.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "20/20 pdf notes rows passed" in r.stdout


def test_pdf_export_table_passes_under_node():
    if not shutil.which("node"):
        pytest.skip("node not available")
    r = subprocess.run(  # noqa: S603 - fixed argv
        [shutil.which("node"), str(ROOT / "tests/js/pdf_export_table.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "22/22 pdf export rows passed" in r.stdout


def test_vendor_files_are_tracked_and_match_recorded_checksums():
    import hashlib

    vendor = ROOT / "static/pdf/vendor"
    lines = (vendor / "SHA256SUMS").read_text().splitlines()
    on_disk = [
        x
        for x in vendor.rglob("*")
        if x.is_file() and x.name not in ("SHA256SUMS", "PROVENANCE.txt")
    ]
    assert len(lines) == len(on_disk) >= 5
    for line in lines:
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == digest, name
    if not shutil.which("git"):
        return
    tracked = subprocess.run(  # noqa: S603 - fixed argv
        [shutil.which("git"), "ls-files", "static/pdf/vendor"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert (
        "static/pdf/vendor/pdf.min.mjs" in tracked
        and "static/pdf/vendor/pdf.worker.min.mjs" in tracked
    )


def test_module_files_are_served_with_a_javascript_type_by_django_static_serving():
    from django.contrib.staticfiles.views import serve
    from django.test import RequestFactory, override_settings

    with override_settings(DEBUG=True):
        for f in ("pdf/vendor/pdf.min.mjs", "pdf/vendor/pdf.worker.min.mjs"):
            r = serve(RequestFactory().get("/static/" + f, HTTP_HOST=HOST), f)
            assert r.status_code == 200 and r["Content-Type"].startswith("text/javascript"), r[
                "Content-Type"
            ]
