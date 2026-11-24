"""Walks key pages as employee, manager and admin (desktop and 390px) and fails on any CSP violation.
Also writes full-page screenshots to $VISUAL_SNAP_DIR (a temp dir when unset).

Used to compare before/after a CSS refactor: run once on the old commit and once on the new one,
then compare the two folders pixel by pixel."""
import os
from pathlib import Path

import pyotp
import pytest

from tests.e2e.test_playwright_e2e_browser import browser, playwright_instance, reset_rate_limiter, seeded_users  # noqa: F401

PAGES = ["/attendance/", "/auth/security/", "/directory/", "/posts/", "/certificates/"]


def test_snapshots(browser, live_server, seeded_users, tmp_path):  # noqa: F811
    out = Path(os.environ.get("VISUAL_SNAP_DIR") or tmp_path)
    violations = []
    _orig = browser.new_context

    def _ctx(*a, **k):
        c = _orig(*a, **k)
        c.on("page", lambda p: p.on("console", lambda m: violations.append(m.text) if "Content Security Policy" in m.text else None))
        return c
    browser.new_context = _ctx
    out.mkdir(parents=True, exist_ok=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.add_init_script("Date.now = () => 1800000000000;")
    for name, path in (("login", "/auth/login/?flow=signin"), ("activate", "/auth/activate/"), ("forgot", "/auth/recovery/forgot-password/"), ("forgot2", "/auth/recovery/forgot-password/?method=method2"), ("forgot3", "/auth/recovery/forgot-password/?method=method3"), ("login_first", "/auth/login/?flow=login")):
        page.goto(f"{live_server.url}{path}")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(out / f"pub_{name}.png"), full_page=True)
    page.goto(f"{live_server.url}/auth/login/?flow=signin")
    page.fill("#employee_id", seeded_users["emp"].employee_id)
    page.fill("#password", "ValidPass2026!@#")
    page.fill("#totp_code", pyotp.TOTP(seeded_users["emp_totp"]).now())
    page.click("#confirm-btn")
    page.wait_for_load_state("networkidle")
    for path in PAGES:
        page.goto(f"{live_server.url}{path}")
        page.wait_for_load_state("networkidle")
        page.evaluate("() => document.querySelectorAll('[id*=clock],[id*=time],time').forEach(e => e.textContent = '')")
        page.screenshot(path=str(out / ("emp_" + path.strip("/").replace("/", "_") + ".png")), full_page=True)
    ctx.close()

    from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
    from apps.accounts.models import TOTPCredential
    from django.utils import timezone

    kr = get_auth_key_ring()
    secrets = {}
    for who in ("admin", "mgr"):
        raw = "JBSWY3DPEHPK3PXP"
        cipher, nonce, key_id = encrypt_secret(raw, kr)
        TOTPCredential.objects.create(user=seeded_users[who], encrypted_secret=cipher, nonce=nonce, key_id=key_id,
                                      confirmed_at=timezone.now(), last_used_step=0)
        secrets[who] = raw
    for who, pages in (("admin", ["/auth/admin/iam/?tab=%s" % x for x in ("identity", "roles", "sessions", "audit", "recovery", "privileged", "overview", "threats")] + ["/attendance/admin/", "/attendance/report/"]),
                       ("mgr", ["/attendance/"])):
        for vp, vw in (("d", 1280), ("m", 390)):
            ctx = browser.new_context(viewport={"width": vw, "height": 800})
            page = ctx.new_page()
            TOTPCredential.objects.filter(user=seeded_users[who]).update(last_used_step=0)
            from django.core.cache import cache
            cache.clear()
            page.goto(f"{live_server.url}/auth/login/?flow=signin")
            page.fill("#employee_id", seeded_users[who].employee_id)
            page.fill("#password", "ValidPass2026!@#")
            page.fill("#totp_code", pyotp.TOTP(secrets[who]).now())
            page.click("#confirm-btn")
            page.wait_for_load_state("networkidle")
            assert "/auth/login" not in page.url, "snapshot login failed"
            for path in pages:
                page.goto(f"{live_server.url}{path}")
                page.wait_for_load_state("networkidle")
                page.evaluate("() => document.querySelectorAll('time,[id*=clock]').forEach(e => e.textContent = '')")
                name = f"{who}_{vp}_" + path.strip("/").replace("/", "_").replace("?", "_").replace("=", "_")
                page.screenshot(path=str(out / (name + ".png")), full_page=True)
                (out / (name + ".url")).write_text(page.url + "\n")
            ctx.close()
    browser.new_context = _orig
    assert violations == [], violations
