"""Cross-account walk for the account-security routes the first walk could not seed (tests/unit/test_cross_account_walk.py):
passkey delete, session terminate, device approval (wait, status, decision, email-otp, face, admin-approve), recovery
console approve and reject, security alert acknowledge, backup integrity check.

Setup: user A owns a passkey, a login session row, a pending second-device request, a pending recovery request, an open
security alert about A; a backup run exists. B is an ordinary employee with step-up (sudo) ACTIVE, so the step-up gate is
not what stops B; the ownership or role check beneath it has to. An admin exists for the role-gated routes.

Asserts for B on each route (GET and POST): no 5xx, no A marker in the body, and a full before/after snapshot of every
row (all fields) unchanged except throttle, session and audit/log tables and B's last_login.
Positive controls, each checked by a visible state change (the backup check by its exact redirect target): A deletes her own session row; A revokes her
own passkey (with sudo); A approves her own device request with the matching number; the admin acknowledges the alert,
rejects the recovery request, and reaches the backup check.

Device approval wait, status, email-otp and face pages are by design reached by the requesting device WITHOUT a signed-in
session, keyed only by the request id. They are excluded from B's walk. Only one GET probe of the wait page exists
(test_device_wait_page_get_probe_with_a_real_request_id); status, email-otp and face, and every POST to those pages, are
untested. Nothing here approves that behaviour.
Step-up is made active for every actor in the fixture (asserted for B), the process-wide step-up cache is cleared before
and after each test, and B's requests are checked for a redirect to the step-up page (any would fail the test).
B sends GET, an empty POST and a valid-payload POST to each of 8 routes. Not controlled by an authorized action: recovery
approve and device admin-approve positive controls are absent (they need second-admin or two-person state not built here).
The snapshot keeps accounts.privilegeelevation rows except B's own, and strips last_activity and last_login everywhere.
Not covered: IAM authentication transaction route (needs an IAM admin identity on the IAM host); one B user; test client
without CSRF; one machine, one run."""

import datetime as dt
import re

import pytest
from django.apps import apps as django_apps
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.auth import stepup
from apps.accounts.auth.stepup import grant_sudo, is_sudo_active
from apps.accounts.models import (
    BackupRun, CustomUser, TOTPCredential, DeviceApprovalRequest, PasskeyCredential, RecoveryRequest, SecurityAlert, UserSession,
)  # fmt: skip

HOST = "app.salowtech.com"
MARK = "ZqAlpha"
# Throttle, Django sessions, auth and face logs and the verification log. Step-up grant rows (accounts.privilegeelevation)
# stay in the snapshot except B's own rows, so a grant for A or anyone else would show as a change.
EXCLUDED = {"accounts.authevent", "accounts.loginattempt", "core.ratelimitbucket", "core.ratelimitrecord", "sessions.session",
            "certificates.certificateverificationlog", "face.faceverificationlog"}  # fmt: skip
BEARER = ("wait", "status", "email-otp", "face")  # device approval pages for the not-yet-signed-in device


def _u(eid, name, role="employee"):
    return CustomUser.objects.create(employee_id=eid, full_name=name, email=f"{eid.lower()}@salowtech.com",
                                     role=role, status="active", is_active=True)  # fmt: skip


def _snapshot(skip_elevation_of=None):
    snap = {}
    for m in django_apps.get_models():
        label = m._meta.label_lower
        if m._meta.proxy or label in EXCLUDED:
            continue
        for row in m.objects.all().values():
            if label == "accounts.privilegeelevation" and row.get("user_id_ref") == skip_elevation_of:
                continue
            row.pop("last_login", None)
            row.pop("last_activity", None)
            snap[(label, str(row.get("id", row.get("pk", row))))] = repr(sorted(row.items(), key=lambda kv: kv[0]))
    return snap


@pytest.fixture
def world(db):
    stepup._USER_SUDO_CACHE.clear()  # process-global; database ids are reused between tests
    a = _u("ZQA-1", f"{MARK} Person")
    b, adm = _u("ZQB-1", "Bravo Person"), _u("ZQS-1", "Admin Person", role="admin")
    sup = _u("ZQS-2", "Super Person", role="super_admin")  # backups are super-admin only; a confirmed authenticator keeps the middleware from forcing MFA setup
    TOTPCredential.objects.create(user=sup, encrypted_secret=b"x", nonce=b"x", key_id="k", confirmed_at=timezone.now())
    w = {"a": a, "b": b, "admin": adm, "super": sup}
    w["passkey"] = PasskeyCredential.objects.create(user=a, credential_id="zqa-cred-1", public_key=b"x", display_name=f"{MARK} key")  # fmt: skip
    w["session"] = UserSession.objects.create(user=a, session_key="zqasessionkey0000000000000000000000001", user_agent=f"{MARK} browser")  # fmt: skip
    w["device"] = DeviceApprovalRequest.objects.create(user=a, requesting_device_agent=f"{MARK} device", number_challenge=42,
                                                       expires_at=timezone.now() + dt.timedelta(minutes=30))  # fmt: skip
    w["recovery"] = RecoveryRequest.objects.create(user=a, reason=f"{MARK} reason")
    w["alert"] = SecurityAlert.objects.create(category=SecurityAlert.Category.choices[0][0], title=f"{MARK} alert", employee_id=a.employee_id)  # fmt: skip
    w["backup"] = BackupRun.objects.create(requested_by_ref=adm.pk, requested_by_employee_id=adm.employee_id)
    for who in ("a", "b", "admin", "super"):
        grant_sudo(w[who])  # step-up active for everyone, so it is never what stops a request
    yield w
    stepup._USER_SUDO_CACHE.clear()


def _urls(w):
    d = w["device"].request_id
    return {
        "passkey-delete": f"/auth/passkeys/{w['passkey'].pk}/delete/",
        "session-terminate": f"/auth/security/sessions/{w['session'].session_key}/terminate/",
        "dev-wait": f"/auth/devices/approval/{d}/wait/", "dev-status": f"/auth/devices/approval/{d}/status/",
        "dev-decision": f"/auth/devices/approval/{d}/decision/", "dev-otp": f"/auth/devices/approval/{d}/email-otp/",
        "dev-face": f"/auth/devices/approval/{d}/face/", "dev-admin": f"/auth/devices/approval/{d}/admin-approve/",
        "rec-approve": f"/auth/recovery/console/{w['recovery'].pk}/approve/",
        "rec-reject": f"/auth/recovery/console/{w['recovery'].pk}/reject/",
        "alert-ack": f"/auth/security/alerts/{w['alert'].pk}/acknowledge/",
        "backup-check": f"/auth/admin/backups/{w['backup'].pk}/check/",
    }  # fmt: skip


def _client(user, n):
    c = Client(HTTP_HOST=HOST, REMOTE_ADDR=f"10.7.0.{n % 250 + 1}")
    c.force_login(user)
    return c


# Valid payloads for B (an authorized user would send the same). Routes with no body take {}.
VALID = {
    "dev-decision": {"decision": "approve", "matching_number": "42"},
    "dev-admin": {},
    "rec-reject": {"reason": "ZqB reason"},
    "rec-approve": {},
    "alert-ack": {"reason": "ZqB reason", "next": "/auth/admin/iam/"},
}
SKIP_FOR_B = {"dev-wait", "dev-status", "dev-otp", "dev-face"}  # bearer-keyed device pages, see the module docstring


def test_b_with_step_up_active_cannot_reach_or_change_a_s_security_objects(world):
    w = world
    assert is_sudo_active(w["b"]), "B's step-up must be active, or a sudo redirect could be what stops B"
    # the same client.post form encoding is what the owner controls use, so a B payload is parsed exactly like theirs
    problems, n, sudo_redirects = [], 0, 0
    before = _snapshot(skip_elevation_of=w["b"].pk)
    for key, url in _urls(w).items():
        if key in SKIP_FOR_B:
            continue
        for method, data in (("get", None), ("post", {}), ("post", VALID.get(key, {}))):
            n += 1
            c = _client(w["b"], n)
            r = c.get(url) if data is None else c.post(url, data)  # form-encoded, same as the owner controls
            if r.status_code >= 500:
                problems.append(f"5xx {r.status_code} {method.upper()} {key}")
            if MARK.encode() in r.content or MARK in r.headers.get("Location", ""):
                problems.append(f"marker in {r.status_code} {method.upper()} {key}")
            if "/sudo" in r.headers.get("Location", ""):
                sudo_redirects += 1
                problems.append(f"B was sent to the step-up page on {method.upper()} {key}: the gate, not ownership, stopped B")
    after = _snapshot(skip_elevation_of=w["b"].pk)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        problems.append(f"rows changed by B: {changed[:10]}")
    assert not problems, "\n".join(problems)
    open("/tmp/cross_auth_counts.txt", "w").write(f"B walk {n} requests (GET, empty POST, valid-payload POST), {len(before)} rows snapshotted, {sudo_redirects} sudo redirects\n")


def test_owner_and_admin_positive_controls_change_state(world):
    w = world
    u = _urls(w)
    ca = _client(w["a"], 1)
    assert ca.post(u["session-terminate"]).status_code in (302, 200)
    assert not UserSession.objects.filter(pk=w["session"].pk).exists(), "A could not end her own session"
    assert ca.post(u["passkey-delete"]).status_code in (302, 200)
    w["passkey"].refresh_from_db()
    assert w["passkey"].is_active is False, "A could not revoke her own passkey"
    ca.post(u["dev-decision"], VALID["dev-decision"])
    w["device"].refresh_from_db()
    assert w["device"].status == DeviceApprovalRequest.Status.APPROVED, "A could not approve her own device request"
    cad = _client(w["admin"], 2)
    ra = cad.post(u["alert-ack"], VALID["alert-ack"])
    w["alert"].refresh_from_db()
    assert w["alert"].status != SecurityAlert.Status.OPEN, f"admin could not acknowledge the alert: {ra.status_code} {ra.headers.get('Location')}"
    cad.post(u["rec-reject"], {"reason": "test"})
    w["recovery"].refresh_from_db()
    assert w["recovery"].status != RecoveryRequest.Status.PENDING, "admin could not reject the recovery request"
    r = _client(w["super"], 3).post(u["backup-check"])
    assert r.status_code == 302 and r.headers["Location"] == reverse("accounts:admin_backups"), (r.status_code, r.headers.get("Location"))
    # not controlled by an authorized action here: recovery approve and device admin-approve (they need two-person or
    # second-admin state that this test does not build)


def test_device_wait_page_get_probe_with_a_real_request_id(world):
    """A single anonymous GET probe of the wait page, with a real request id and with a made-up one. It records what the
    page shows today. It is NOT a pin of the four device pages: status, email-otp and face (any method) and the POSTs of
    those pages were not tested, and nothing here approves the behaviour."""
    w = world
    u = _urls(w)
    anon = Client(HTTP_HOST=HOST)
    wait = anon.get(u["dev-wait"])
    seen = {"status": wait.status_code, "has_name": MARK.encode() in wait.content,
            "has_employee_id": b"ZQA-1" in wait.content, "has_number": b">42<" in wait.content.replace(b" ", b"").replace(b"\n", b"")}  # fmt: skip
    open("/tmp/cross_auth_bearer.txt", "w").write(repr(seen) + "\n")
    other = anon.get(u["dev-wait"].replace(str(w["device"].request_id), "00000000-0000-0000-0000-000000000000"))
    assert MARK.encode() not in other.content
