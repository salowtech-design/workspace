import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_formula_function_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the formula table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/formula_table.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "405/405 rows passed" in r.stdout


def test_model_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the model table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/model_table.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "111/111 model rows passed" in r.stdout


def test_formula_batch2_passes_in_node():
    assert shutil.which("node"), "node is required to run the formula table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/formula_batch2.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "63/63 rows passed" in r.stdout


def test_structure_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the structure table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/sheets_struct.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "60/60 structure rows passed" in r.stdout


def test_structure_storage_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the structure storage table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/sheets_struct_store.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "33/33 structure storage rows passed" in r.stdout


def test_csv_import_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the CSV import table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/sheets_csv_import.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "23/23 csv import rows passed" in r.stdout


def test_freeze_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the freeze table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/sheets_freeze.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "12/12 freeze rows passed" in r.stdout


def test_versions_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the versions table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/sheets_versions.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "20/20 version rows passed" in r.stdout


def test_xtab_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the cross-tab table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/sheets_xtab.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "34/34 cross-tab rows passed" in r.stdout


def test_widths_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the column widths table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/sheets_widths.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "33/33 width rows passed" in r.stdout


def test_scatter_table_passes_in_node():
    assert shutil.which("node"), "node is required to run the scatter table"
    r = subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [shutil.which("node"), str(ROOT / "tests/js/sheets_scatter.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "9/9 scatter rows passed" in r.stdout
