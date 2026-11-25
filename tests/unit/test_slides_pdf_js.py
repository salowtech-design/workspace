import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_slides_pdf_rows_pass_in_node():
    assert shutil.which("node"), "node is required to run the slides pdf rows"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/slides_pdf.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "13/13 slides pdf rows passed" in r.stdout
