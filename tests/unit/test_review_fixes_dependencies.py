"""Regression tests for dependency declaration and undefined-name fixes."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pymysql_declared_because_config_imports_it():
    assert "import pymysql" in (ROOT / "config" / "__init__.py").read_text()
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    assert any(d.lower().startswith("pymysql") for d in deps)


def test_pymysql_present_in_lock_file():
    assert 'name = "pymysql"' in (ROOT / "uv.lock").read_text()
