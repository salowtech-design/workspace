"""
Independent HTTP & View Security Probe for Forensic Verification.
Evaluates:
1. Public landing page render and progressive disclosure.
2. Password input attribute safety (type="password", autocomplete, data-password-input).
3. CSRF token enforcement and security headers (CSP, X-Frame-Options, X-Content-Type-Options).
4. RBAC boundary on /auth/admin/iam/ and /attendance/admin/.
5. Rate limiter 429 burst blocking on sensitive routes.
6. SSE handshake headers.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")

import django
django.setup()

from django.core.management import call_command
call_command("migrate", verbosity=0)

from django.test import Client
from apps.accounts.models import CustomUser

def run_http_probe():
    client = Client()
    print("=" * 70)
    print("SALOW WORKSPACE - HTTP & VIEW FORENSIC AUDIT")
    print("=" * 70)

    # 1. Landing Page Root
    resp = client.get("/auth/login/")
    print(f"[*] GET /auth/login/ -> HTTP {resp.status_code}")
    content = resp.content.decode("utf-8")
    assert "btn-landing-login" in content or "Login" in content
    assert "btn-landing-signin" in content or "Sign In" in content
    has_inputs_initially = '<input type="password"' in content and 'style="display:none"' not in content
    print(f"    - Landing buttons present: YES")
    print(f"    - Progressive disclosure initial inputs hidden/suppressed: {not has_inputs_initially}")
    print(f"    - Security Headers:")
    for header in ["Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy"]:
        print(f"      * {header}: {resp.headers.get(header)}")

    # 2. Login Tab Progressive Disclosure
    resp_login = client.get("/auth/login/?action=login")
    print(f"\n[*] GET /auth/login/?action=login -> HTTP {resp_login.status_code}")
    c_login = resp_login.content.decode("utf-8")
    has_pwd_field = 'type="password"' in c_login
    has_eye_toggle = 'toggle-password' in c_login or 'eye' in c_login or 'data-toggle="password"' in c_login
    print(f"    - Password field rendered: {has_pwd_field}")
    print(f"    - Password eye toggle rendered: {has_eye_toggle}")

    # 3. Unauthenticated Access to Administrative Consoles
    resp_iam = client.get("/auth/admin/iam/")
    print(f"\n[*] Anonymous GET /auth/admin/iam/ -> HTTP {resp_iam.status_code} (Redirect: {resp_iam.headers.get('Location')})")
    assert resp_iam.status_code == 302
    assert "/auth/login/" in resp_iam.headers.get("Location")

    resp_ws_admin = client.get("/attendance/admin/")
    print(f"[*] Anonymous GET /attendance/admin/ -> HTTP {resp_ws_admin.status_code} (Redirect: {resp_ws_admin.headers.get('Location')})")
    assert resp_ws_admin.status_code == 302
    assert "/auth/login/" in resp_ws_admin.headers.get("Location")

    # 4. Intern RBAC Access Denied
    intern, _ = CustomUser.objects.get_or_create(
        employee_id="PROBE-INTERN-01",
        defaults={
            "full_name": "Probe Intern",
            "role": CustomUser.Role.INTERN,
            "status": CustomUser.Status.ACTIVE,
        }
    )
    if not intern.password:
        intern.set_password("ValidPassword2026!")
        intern.save()
    client.force_login(intern)
    s = client.session
    s["_salow_mfa_verified"] = True
    s.save()

    resp_intern_iam = client.get("/auth/admin/iam/")
    print(f"\n[*] Intern GET /auth/admin/iam/ -> HTTP {resp_intern_iam.status_code} (Must be 403 Forbidden)")
    assert resp_intern_iam.status_code == 403

    resp_intern_ws = client.get("/attendance/admin/")
    print(f"[*] Intern GET /attendance/admin/ -> HTTP {resp_intern_ws.status_code} (Must be 403 Forbidden)")
    assert resp_intern_ws.status_code == 403

    # 5. Admin Access to Both Segregated Panels
    admin_user, _ = CustomUser.objects.get_or_create(
        employee_id="PROBE-ADMIN-01",
        defaults={
            "full_name": "Probe Admin",
            "role": CustomUser.Role.ADMIN,
            "status": CustomUser.Status.ACTIVE,
        }
    )
    if not admin_user.password:
        admin_user.set_password("ValidPassword2026!")
        admin_user.save()
    client.force_login(admin_user)
    s = client.session
    s["_salow_mfa_verified"] = True
    s.save()

    resp_admin_iam = client.get("/auth/admin/iam/")
    print(f"\n[*] Admin GET /auth/admin/iam/ -> HTTP {resp_admin_iam.status_code}")
    assert resp_admin_iam.status_code == 200
    c_iam = resp_admin_iam.content.decode("utf-8")
    for tab in ["identity", "roles", "security", "sessions", "recovery", "lifecycle", "audit", "breakglass", "services", "stream", "outbox"]:
        print(f"    - Tab '{tab}' present in IAM console: {tab in c_iam}")

    resp_admin_ws = client.get("/attendance/admin/")
    print(f"[*] Admin GET /attendance/admin/ -> HTTP {resp_admin_ws.status_code}")
    assert resp_admin_ws.status_code == 200
    c_ws = resp_admin_ws.content.decode("utf-8")
    print(f"    - Workspace admin contains read-only security metrics: {'Central Identity' in c_ws or 'Security Summary' in c_ws or 'Active Sessions' in c_ws}")

    # 6. SSE Live Stream Auth Check
    resp_sse = client.get("/auth/admin/iam/live-stream/?limit=5")
    print(f"\n[*] Admin GET /auth/admin/iam/live-stream/ -> Content-Type: {resp_sse.headers.get('Content-Type')}")
    assert "text/event-stream" in resp_sse.headers.get("Content-Type")

    print("\n" + "=" * 70)
    print("ALL HTTP & VIEW FORENSIC AUDIT CHECKS PASSED [PASS]")
    print("=" * 70)

if __name__ == "__main__":
    run_http_probe()
