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


def test_compute_payload_digest_serialises_dicts_and_lists():
    """iam_auth used json without importing it, so dict payloads raised NameError."""
    from apps.accounts.auth.iam_auth import compute_payload_digest

    a = compute_payload_digest({"b": 1, "a": [1, 2]})
    b = compute_payload_digest({"a": [1, 2], "b": 1})
    assert a == b and len(a) == 64
    assert compute_payload_digest(["x"]) != compute_payload_digest(["y"])
    assert compute_payload_digest("plain") == compute_payload_digest("plain")
