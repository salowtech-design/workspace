"""style-src no longer allows 'unsafe-inline': nothing in templates, scripts or HTML built in Python may use a style attribute."""
import re
from pathlib import Path

import pytest
from django.test import Client

ROOT = Path(__file__).resolve().parents[2]
ATTR = re.compile(r"""<[a-zA-Z][^<>]*\sstyle\s*=""")


def test_no_style_attributes_in_templates():
    bad = [str(p) for p in (ROOT / "templates").rglob("*.html") if ATTR.search(p.read_text())]
    assert bad == []


def test_no_style_attributes_in_static_js_or_python_html():
    paths = [p for p in (ROOT / "static").glob("*.js")] + list((ROOT / "apps").rglob("views/*.py")) + [ROOT / "apps/certificates/services.py"]
    bad = [str(p) for p in paths if re.search(r"""\sstyle\s*=\s*["']""", p.read_text()) or "setAttribute('style'" in p.read_text()]
    assert bad == []


def test_style_blocks_carry_nonce():
    for p in (ROOT / "templates").rglob("*.html"):
        for m in re.finditer(r"<style\b([^>]*)>", p.read_text()):
            assert "nonce=" in m.group(1), str(p)


def test_generated_stylesheet_is_loaded_by_base_and_standalone_pages():
    assert "inline-styles.css" in (ROOT / "templates/base.html").read_text()
    assert "inline-styles.css" in (ROOT / "templates/certificates/verify.html").read_text()
    assert (ROOT / "static/css/inline-styles.css").stat().st_size > 1000


@pytest.mark.django_db
def test_style_src_has_nonce_and_no_unsafe_inline():
    h = Client(HTTP_HOST="localhost").get("/auth/login/").headers["Content-Security-Policy"]
    style = h.split("style-src ")[1].split(";")[0]
    assert "'unsafe-inline'" not in style and "nonce-" in style
    assert "http" not in style
