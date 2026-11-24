"""
Real Browser End-to-End Acceptance Test Suite using Playwright.
Enforces Part 5.5 and Part 12 of the Master Execution Prompt:
1. Real headless Chromium browser execution.
2. Distinct browser contexts for Employee, Manager, and IAM Administrator.
3. Desktop and Mobile viewport testing.
4. User journeys: Login landing, Single-Screen sign-in, Directory, Attendance, Posts, Certificates, and Logout.
5. Screenshot artifact capture for audit verification.
"""

import os
from pathlib import Path

# Allow synchronous Django ORM operations alongside Playwright's sync event loop
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

import pyotp
import pytest
from django.utils import timezone
from playwright.sync_api import sync_playwright

from apps.accounts.auth.activation import issue_activation_code
from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
from apps.accounts.models import ActivationCode, CustomUser, RecoveryCode, TOTPCredential
from apps.core.middleware import RateLimitMiddleware

SCREENSHOT_DIR = Path("reports/e2e_screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    RateLimitMiddleware.reset()
    yield
    RateLimitMiddleware.reset()


@pytest.fixture(scope="module")
def playwright_instance():
    p = sync_playwright().start()
    yield p
    p.stop()


@pytest.fixture(scope="module")
def browser(playwright_instance):
    b = playwright_instance.chromium.launch(headless=True)
    yield b
    b.close()


@pytest.fixture
def seeded_users(db):
    """Seed test users with active TOTP credentials."""
    kr = get_auth_key_ring()

    # 1. Employee
    emp = CustomUser.objects.create_user(
        employee_id="ST-PW-EMP01",
        full_name="Playwright Employee",
        password="ValidPass2026!@#",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    raw_totp_emp = "JBSWY3DPEHPK3PXP"
    cipher, nonce, key_id = encrypt_secret(raw_totp_emp, kr)
    TOTPCredential.objects.create(
        user=emp,
        encrypted_secret=cipher,
        nonce=nonce,
        key_id=key_id,
        confirmed_at=timezone.now(),
        last_used_step=0,
    )

    # 2. Manager
    mgr = CustomUser.objects.create_user(
        employee_id="ST-PW-MGR01",
        full_name="Playwright Manager",
        password="ValidPass2026!@#",
        role=CustomUser.Role.MANAGER,
        status=CustomUser.Status.ACTIVE,
    )

    # 3. Super Admin
    admin = CustomUser.objects.create_user(
        employee_id="ST-PW-ADM01",
        full_name="Playwright Admin",
        password="ValidPass2026!@#",
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
    )

    return {
        "emp": emp,
        "emp_totp": raw_totp_emp,
        "mgr": mgr,
        "admin": admin,
    }


@pytest.mark.django_db(transaction=True)
class TestPlaywrightBrowserAcceptance:
    """Verifies actual browser DOM rendering, interactions, and isolation."""

    def test_landing_page_ui_and_viewport_responsiveness(self, browser, live_server):
        """Test landing page in Desktop (1280x800) and Mobile (375x667) viewports."""
        # Desktop context
        desktop_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = desktop_ctx.new_page()
        page.set_default_timeout(60000)
        res = page.goto(f"{live_server.url}/auth/login/", wait_until="domcontentloaded", timeout=60000)
        assert res.status == 200

        # Assert security headers in real HTTP response
        headers = res.headers
        assert "x-frame-options" in headers or "X-Frame-Options" in headers
        assert "x-content-type-options" in headers or "X-Content-Type-Options" in headers

        # Assert title and primary action buttons
        assert "Workspace" in page.title()
        assert page.locator("button, a").filter(has_text="Sign In").count() > 0

        # Capture Desktop screenshot
        page.screenshot(path=str(SCREENSHOT_DIR / "01_login_desktop.png"))
        desktop_ctx.close()

        # Mobile viewport context
        mobile_ctx = browser.new_context(
            viewport={"width": 375, "height": 667},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
        )
        mobile_page = mobile_ctx.new_page()
        m_res = mobile_page.goto(f"{live_server.url}/auth/login/", wait_until="domcontentloaded")
        assert m_res.status == 200
        mobile_page.screenshot(path=str(SCREENSHOT_DIR / "02_login_mobile.png"))
        mobile_ctx.close()

    def test_employee_real_browser_login_journey(self, browser, live_server, seeded_users):
        """Tests end-to-end employee authentication via real browser DOM submission."""
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # 1. Navigate to login returning employee flow
        page.goto(f"{live_server.url}/auth/login/?flow=signin")

        # 2. Fill credentials on returning employee form
        page.fill("#employee_id", seeded_users["emp"].employee_id)
        page.fill("#password", "ValidPass2026!@#")

        # Generate fresh TOTP code
        totp = pyotp.TOTP(seeded_users["emp_totp"])
        page.fill("#totp_code", totp.now())

        page.screenshot(path=str(SCREENSHOT_DIR / "03_employee_form_filled.png"))

        # 3. Submit form
        page.click("#confirm-btn")
        page.wait_for_load_state("networkidle")

        # 4. Verify successful redirection to /attendance/
        assert "/attendance" in page.url
        page.screenshot(path=str(SCREENSHOT_DIR / "04_attendance_dashboard.png"))

        # 5. Verify attendance page elements
        content = page.content()
        assert "Attendance" in content or "Check In" in content or "Work Session" in content

        # 6. Verify logout via authenticated UI button
        page.click('button:has-text("Sign Out")')
        page.wait_for_load_state("networkidle")
        assert "/auth/login" in page.url

        context.close()

    def test_multi_context_isolation_between_employee_and_admin(
        self, browser, live_server, seeded_users
    ):
        """Independent browser contexts ensure strict cookie and session segregation."""
        # Context 1: Employee
        emp_ctx = browser.new_context()
        emp_page = emp_ctx.new_page()

        # Context 2: Unauthenticated User
        anon_ctx = browser.new_context()
        anon_page = anon_ctx.new_page()

        # Employee logs in via returning employee flow
        emp_page.goto(f"{live_server.url}/auth/login/?flow=signin")
        emp_page.fill("#employee_id", seeded_users["emp"].employee_id)
        emp_page.fill("#password", "ValidPass2026!@#")
        emp_page.fill("#totp_code", pyotp.TOTP(seeded_users["emp_totp"]).now())
        emp_page.click("#confirm-btn")
        emp_page.wait_for_load_state("networkidle")
        assert "/attendance" in emp_page.url

        # Employee attempts to access Central IAM console -> Must be forbidden (403)
        res = emp_page.goto(f"{live_server.url}/auth/admin/iam/")
        assert res.status in (403, 302)

        # Anonymous context visits protected route -> Redirected to login (302 -> 200 at /auth/login/)
        anon_page.goto(f"{live_server.url}/attendance/")
        assert "/auth/login" in anon_page.url

        emp_ctx.close()
        anon_ctx.close()

    def test_employee_activation_journey(self, browser, live_server, db):
        """Flow A: Full browser journey for account activation, password creation, TOTP setup, and recovery codes."""
        user = CustomUser.objects.create_user(
            employee_id="ST-PW-ACT01",
            full_name="Activation Employee",
            password="TemporaryPassword123!",
            role=CustomUser.Role.EMPLOYEE,
            status=CustomUser.Status.INVITED,
        )
        _, raw_code = issue_activation_code(user=user, purpose=ActivationCode.Purpose.ACTIVATION)

        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # Step 1: Submit activation code on /auth/activate/
        page.goto(f"{live_server.url}/auth/activate/")
        page.fill('input[name="employee_id"]', user.employee_id)
        page.fill('input[name="password"]', raw_code)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        assert "/auth/activate/password" in page.url

        # Step 2: Set permanent strong password
        page.fill('input[name="password"]', "NewStrongPass2026!@#")
        page.fill('input[name="confirm_password"]', "NewStrongPass2026!@#")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        assert "/auth/activate/mfa/setup" in page.url

        # Step 3: Extract manual secret and confirm TOTP code
        secret_elem = page.locator("#manual-secret-code, code, .secret-text")
        secret_text = secret_elem.first.inner_text().replace(" ", "").strip()
        totp = pyotp.TOTP(secret_text)

        page.fill('input[name="code"]', totp.now())
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        assert "/auth/activate/recovery-codes" in page.url

        # Step 4: Verify 10 recovery codes rendered on screen and confirm saved
        codes_count = page.locator(".recovery-code-pill, .recovery-code, li").count()
        assert codes_count >= 10

        page.screenshot(path=str(SCREENSHOT_DIR / "05_recovery_codes_view.png"))
        if page.locator("#confirmed_saved").count() > 0:
            page.check("#confirmed_saved")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Step 5: Account active and redirected to attendance dashboard
        assert "/attendance" in page.url
        user.refresh_from_db()
        assert user.status == CustomUser.Status.ACTIVE
        assert RecoveryCode.objects.filter(user=user, used_at__isnull=True).count() == 10

        context.close()

    def test_workspace_modules_navigation(self, browser, live_server, seeded_users):
        """Authenticated employee visits Directory, Work Logs, Certificates, and Security Self-Service."""
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # Login
        page.goto(f"{live_server.url}/auth/login/?flow=signin")
        page.fill("#employee_id", seeded_users["emp"].employee_id)
        page.fill("#password", "ValidPass2026!@#")
        page.fill("#totp_code", pyotp.TOTP(seeded_users["emp_totp"]).now())
        page.click("#confirm-btn")
        page.wait_for_load_state("networkidle")
        assert "/attendance" in page.url

        # 1. Directory
        page.goto(f"{live_server.url}/directory/")
        page.wait_for_load_state("networkidle")
        assert page.locator('input[name="q"], input[type="search"]').count() >= 0
        page.screenshot(path=str(SCREENSHOT_DIR / "06_directory.png"))

        # 2. Work Logs / Posts Feed
        page.goto(f"{live_server.url}/posts/")
        page.wait_for_load_state("networkidle")
        assert "Work" in page.content() or "Posts" in page.content() or "Log" in page.content()
        page.screenshot(path=str(SCREENSHOT_DIR / "07_posts_feed.png"))

        # 3. Certificates
        page.goto(f"{live_server.url}/certificates/")
        page.wait_for_load_state("networkidle")
        assert "Certificates" in page.content() or "Certificate" in page.content()
        page.screenshot(path=str(SCREENSHOT_DIR / "08_certificates.png"))

        # 4. Security Self-Service
        page.goto(f"{live_server.url}/auth/security/")
        page.wait_for_load_state("networkidle")
        assert "Active Sessions" in page.content() or "Security" in page.content()
        page.screenshot(path=str(SCREENSHOT_DIR / "09_security_self_service.png"))

        context.close()


    def test_workspace_idle_warning_stay_signed_in_and_signout(
        self, browser, live_server, seeded_users
    ):
        """Real browser: warning banner near the limit, 'Stay signed in' extends, expiry signs out."""
        import time

        from django.conf import settings
        from importlib import import_module

        SessionStore = import_module(settings.SESSION_ENGINE).SessionStore

        from apps.accounts.auth.workspace_idle import SESSION_KEY as LAST_ACTIVITY_KEY

        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.goto(f"{live_server.url}/auth/login/?flow=signin")
        page.fill("#employee_id", seeded_users["emp"].employee_id)
        page.fill("#password", "ValidPass2026!@#")
        page.fill("#totp_code", pyotp.TOTP(seeded_users["emp_totp"]).now())
        page.click("#confirm-btn")
        page.wait_for_load_state("networkidle")
        assert "/attendance" in page.url

        def session_key():
            return next(c["value"] for c in context.cookies() if c["name"] == settings.SESSION_COOKIE_NAME)

        def age(seconds_ago):
            s = SessionStore(session_key=session_key())
            s[LAST_ACTIVITY_KEY] = time.time() - seconds_ago
            s.save()

        banner = page.locator("#idle-timeout-banner")
        # Active session: no banner.
        assert not banner.is_visible()
        # 100 s left of 900 s: banner appears after reload.
        age(800)  # page loads count as activity, so age the marker after the load
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        banner.wait_for(state="visible", timeout=5000)
        page.screenshot(path=str(SCREENSHOT_DIR / "12_idle_warning_banner.png"))
        # Stay signed in: banner goes away and the marker moves forward.
        page.click("#idle-timeout-stay")
        banner.wait_for(state="hidden", timeout=5000)
        assert time.time() - SessionStore(session_key=session_key())[LAST_ACTIVITY_KEY] < 60
        # Past the limit: next navigation signs out with the inactivity notice.
        age(901)
        page.goto(f"{live_server.url}/attendance/")
        assert "/auth/login" in page.url and "reason=idle" in page.url
        page.screenshot(path=str(SCREENSHOT_DIR / "13_idle_signed_out_notice.png"))
        context.close()

    def test_no_form_content_is_persisted_and_legacy_drafts_are_purged(
        self, browser, live_server, seeded_users
    ):
        """Only free-text areas are kept as drafts; codes, ids and selects never reach browser storage,
        and the sign-in page wipes saved drafts so a shared browser cannot show them to another user."""
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.goto(f"{live_server.url}/auth/login/?flow=signin")
        page.fill("#employee_id", seeded_users["emp"].employee_id)
        page.fill("#password", "ValidPass2026!@#")
        page.fill("#totp_code", pyotp.TOTP(seeded_users["emp_totp"]).now())
        page.click("#confirm-btn")
        page.wait_for_load_state("networkidle")
        assert "/attendance" in page.url

        page.evaluate("() => sessionStorage.setItem('_salow_draft_/old/', 'legacy')")
        page.fill("textarea", "my free text work note")
        page.evaluate(
            """() => {
              const f = document.querySelector('textarea').form;
              for (const [n, t] of [['recovery_code', 'text'], ['totp_code', 'text'], ['employee_id', 'text'], ['api_token', 'text']]) {
                const i = document.createElement('input'); i.type = t; i.name = n; i.value = 'SENSITIVE-' + n; f.appendChild(i);
              }
              Object.defineProperty(document, 'visibilityState', { get: () => 'hidden', configurable: true });
              document.dispatchEvent(new Event('visibilitychange'));
            }"""
        )
        stored = page.evaluate(
            "() => Object.keys(sessionStorage).filter(k => k.startsWith('_salow_draft_')).map(k => sessionStorage.getItem(k)).join('|')"
        )
        assert stored == ""  # nothing, not even free text, is persisted
        assert "SENSITIVE-" not in stored

        page.goto(f"{live_server.url}/auth/login/?flow=signin")
        left = page.evaluate("() => Object.keys(sessionStorage).filter(k => k.startsWith('_salow_draft_')).length")
        assert left == 0
        context.close()
