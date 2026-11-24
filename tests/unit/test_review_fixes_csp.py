"""Regression: templates carry no inline handlers or nonce-less inline scripts; CSP has a nonce."""
import re
from pathlib import Path

import pytest
from django.test import Client

TEMPLATES = list(Path("templates").rglob("*.html"))


def test_no_inline_event_handlers_in_templates():
    bad = [str(p) for p in TEMPLATES if re.search(r"\son[a-z]+\s*=\s*[\"']", p.read_text())]
    assert bad == []


def test_every_inline_script_has_nonce():
    bad = []
    for p in TEMPLATES:
        for m in re.finditer(r"<script\b([^>]*)>", p.read_text()):
            if "src=" not in m.group(1) and "nonce=" not in m.group(1):
                bad.append(str(p))
    assert bad == []


@pytest.mark.django_db
def test_csp_header_uses_fresh_nonce_without_unsafe_inline_scripts():
    c = Client(HTTP_HOST="localhost")
    h1 = c.get("/auth/login/").headers["Content-Security-Policy"]
    h2 = c.get("/auth/login/").headers["Content-Security-Policy"]
    script = h1.split("script-src ")[1].split(";")[0]
    assert "'unsafe-inline'" not in script and "nonce-" in script
    assert "script-src-attr 'none'" in h1
    assert h1 != h2
