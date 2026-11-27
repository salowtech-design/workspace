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
    assert "26/26 structure storage rows passed" in r.stdout
